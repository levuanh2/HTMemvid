
import os
import time
import psutil
import json
import csv
import numpy as np
import scipy.stats
import faiss
from sentence_transformers import SentenceTransformer
from typing import List, Dict, Any, Tuple

# Configuration
BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_FILE = os.path.join(BENCHMARK_DIR, "documents.json")
OUTPUT_JSON = os.path.join(BENCHMARK_DIR, "full_pipeline_benchmark.json")
OUTPUT_CSV = os.path.join(BENCHMARK_DIR, "full_pipeline_summary.csv")

MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 50
NUM_QUERIES = 100
WARMUP_QUERIES = 10
ITERATIONS = 5

class BenchmarkEngine:
    def __init__(self):
        self.raw_results = []
        self.stats = {}

    def get_memory_mb(self):
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

    def generate_synthetic_docs(self, count=500):
        docs = []
        base_text = "Artificial intelligence (AI) is intelligence demonstrated by machines, as opposed to natural intelligence displayed by animals including humans. " * 10
        for i in range(count):
            docs.append({"id": f"syn_{i}", "text": base_text + f" Document ID: {i}"})
        return docs

    def run_iteration(self, iteration_id):
        iter_metrics = {}
        
        # PHASE A: Model Loading (Cold Start)
        t0 = time.perf_counter()
        model = SentenceTransformer(MODEL_NAME)
        t1 = time.perf_counter()
        iter_metrics["model_load_time"] = t1 - t0

        # PHASE B: File Loading
        t0 = time.perf_counter()
        if os.path.exists(DOCS_FILE):
            with open(DOCS_FILE, 'r', encoding='utf-8') as f:
                docs = json.load(f)
        else:
            docs = self.generate_synthetic_docs()
        t1 = time.perf_counter()
        iter_metrics["file_load_time"] = t1 - t0
        iter_metrics["doc_count"] = len(docs)

        # PHASE C: Text Chunking
        t0 = time.perf_counter()
        chunks = []
        for doc in docs:
            text = doc.get("text", "")
            for i in range(0, len(text), CHUNK_SIZE - CHUNK_OVERLAP):
                chunks.append(text[i : i + CHUNK_SIZE])
        t1 = time.perf_counter()
        iter_metrics["chunk_time"] = t1 - t0
        iter_metrics["chunk_count"] = len(chunks)

        # PHASE D: Embedding
        t0 = time.perf_counter()
        embeddings = model.encode(chunks, batch_size=32, show_progress_bar=False, convert_to_numpy=True)
        t1 = time.perf_counter()
        iter_metrics["embed_time"] = t1 - t0
        iter_metrics["embed_dim"] = embeddings.shape[1]

        # PHASE E: Indexing
        start_mem = self.get_memory_mb()
        t0 = time.perf_counter()
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index.add(embeddings)
        t1 = time.perf_counter()
        end_mem = self.get_memory_mb()
        
        iter_metrics["index_time"] = t1 - t0
        iter_metrics["peak_memory_mb"] = end_mem - start_mem
        iter_metrics["index_size"] = index.ntotal

        # PHASE F: Query Benchmark
        # Warm-up
        warmup_q = ["warmup query"] * WARMUP_QUERIES
        warmup_emb = model.encode(warmup_q, convert_to_numpy=True)
        index.search(warmup_emb, k=5)

        # Actual Query Test
        queries = [f"benchmark query {i} machine learning AI" for i in range(NUM_QUERIES)]
        
        encode_times = []
        search_times = []
        total_latencies = []
        
        for q in queries:
            t_start = time.perf_counter()
            
            # 1. Encode
            t_enc_start = time.perf_counter()
            q_emb = model.encode([q], convert_to_numpy=True)
            t_enc_end = time.perf_counter()
            
            # 2. Search
            t_search_start = time.perf_counter()
            D, I = index.search(q_emb, k=5)
            t_search_end = time.perf_counter()
            
            t_end = time.perf_counter()
            
            encode_times.append(t_enc_end - t_enc_start)
            search_times.append(t_search_end - t_search_start)
            total_latencies.append(t_end - t_start)

        iter_metrics["query_encode_mean"] = np.mean(encode_times)
        iter_metrics["query_search_mean"] = np.mean(search_times)
        iter_metrics["query_total_mean"] = np.mean(total_latencies)
        iter_metrics["query_throughput"] = NUM_QUERIES / sum(total_latencies)
        
        # Raw latencies for detailed stats later if needed (keeping means per iter for multi-run stats)
        iter_metrics["raw_latencies"] = total_latencies

        # Calculated totals
        iter_metrics["cold_start_total"] = (
            iter_metrics["model_load_time"] + 
            iter_metrics["file_load_time"] + 
            iter_metrics["chunk_time"] + 
            iter_metrics["embed_time"] + 
            iter_metrics["index_time"]
        )
        
        iter_metrics["warm_start_total"] = (
            iter_metrics["embed_time"] + 
            iter_metrics["index_time"] 
            # Note: Warm start usually implies re-indexing is needed for new data, 
            # or just querying if data is static. 
            # Based on user definition: "Model loaded. Measure embedding + indexing + query" 
            # But query is multiple. Just summing pipeline steps here.
        )
        
        return iter_metrics

    def calculate_statistics(self, values):
        n = len(values)
        if n < 2:
            return np.mean(values), 0.0, 0.0
        
        mean = np.mean(values)
        std = np.std(values, ddof=1)
        se = std / np.sqrt(n)
        ci = se * scipy.stats.t.ppf((1 + 0.95) / 2., n-1)
        return mean, std, ci

    def run(self):
        print(f"Starting Benchmark ({ITERATIONS} iterations)...")
        
        for i in range(ITERATIONS):
            print(f"Running iteration {i+1}/{ITERATIONS}...")
            metrics = self.run_iteration(i)
            self.raw_results.append(metrics)

        self.process_results()
        self.save_json()
        self.save_csv()
        self.print_summary()

    def process_results(self):
        # Keys to aggregate
        keys = [
            "model_load_time", "file_load_time", "chunk_time", 
            "embed_time", "index_time", "peak_memory_mb",
            "query_encode_mean", "query_search_mean", "query_total_mean",
            "cold_start_total", "warm_start_total", "query_throughput"
        ]
        
        final_stats = {}
        for k in keys:
            vals = [r[k] for r in self.raw_results]
            mean, std, ci = self.calculate_statistics(vals)
            final_stats[k] = {
                "mean": mean,
                "std": std,
                "ci_95": ci
            }
        self.stats = final_stats

    def save_json(self):
        output = {
            "cold_start": {
                "total_time": self.stats["cold_start_total"],
                "model_load": self.stats["model_load_time"],
                "file_load": self.stats["file_load_time"]
            },
            "warm_start": {
                "total_indexing_time": self.stats["warm_start_total"], # Embed + Index
                "embed_time": self.stats["embed_time"],
                "index_time": self.stats["index_time"]
            },
            "query_stats": {
                "encode_time": self.stats["query_encode_mean"],
                "search_time": self.stats["query_search_mean"],
                "total_latency": self.stats["query_total_mean"],
                "throughput_qps": self.stats["query_throughput"]
            },
            "system_stats": {
                "peak_memory_mb": self.stats["peak_memory_mb"],
                "chunk_time": self.stats["chunk_time"]
            },
            "raw_iterations": str(len(self.raw_results))
        }

        # Convert numpy types
        def convert(o):
            if isinstance(o, np.generic): return o.item()
            raise TypeError

        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=4, default=convert)

    def save_csv(self):
        headers = ["Initial Phase", "Metric", "Mean", "Std", "95% CI"]
        rows = []
        
        mapping = [
            ("Model Loading", "model_load_time"),
            ("File Loading", "file_load_time"),
            ("Chunking", "chunk_time"),
            ("Embedding", "embed_time"),
            ("Indexing", "index_time"),
            ("Memory Usage", "peak_memory_mb"),
            ("Query Encoding", "query_encode_mean"),
            ("Query Search", "query_search_mean"),
            ("Query Total", "query_total_mean"),
            ("Throughput", "query_throughput")
        ]

        for phase, key in mapping:
            s = self.stats[key]
            rows.append({
                "Initial Phase": phase, 
                "Metric": key, 
                "Mean": s["mean"], 
                "Std": s["std"], 
                "95% CI": s["ci_95"]
            })
            
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def print_summary(self):
        print("\n" + "="*80)
        print(f"{'PHASE':<20} | {'METRIC':<20} | {'MEAN ± STD':<25} | {'95% CI':<10}")
        print("-" * 80)
        
        order = [
            ("Model Load", "model_load_time"),
            ("File Load", "file_load_time"),
            ("Chunking", "chunk_time"),
            ("Embedding", "embed_time"),
            ("Indexing", "index_time"),
            ("Memory (MB)", "peak_memory_mb"),
            ("Query Latency", "query_total_mean"),
            ("Throughput", "query_throughput")
        ]
        
        for name, key in order:
            s = self.stats[key]
            val_str = f"{s['mean']:.4f} ± {s['std']:.4f}"
            ci_str = f"± {s['ci_95']:.4f}"
            print(f"{name:<20} | {key:<20} | {val_str:<25} | {ci_str:<10}")
        print("="*80 + "\n")

if __name__ == "__main__":
    benchmark = BenchmarkEngine()
    benchmark.run()
