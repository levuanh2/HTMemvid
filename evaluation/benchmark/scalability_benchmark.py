
import os
import time
import json
import csv
import psutil
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from typing import Dict, Any, List

# Configuration
BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(BENCHMARK_DIR, "scalability_results.json")
OUTPUT_CSV = os.path.join(BENCHMARK_DIR, "scalability_table.csv")

MODEL_NAME = "all-MiniLM-L6-v2"
CORPUS_SIZES = [100, 500, 1000, 2000]
CHUNK_SIZE = 512
NUM_QUERIES = 100
WARMUP_QUERIES = 10
ITERATIONS = 3

class ScalabilityBenchmark:
    def __init__(self):
        self.results = {}
        self.model = None

    def get_memory_mb(self):
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

    def generate_synthetic_data(self, size):
        docs = []
        base_text = "Machine learning (ML) is a field of inquiry devoted to understanding and building methods that 'learn', that is, methods that leverage data to improve performance on some set of tasks. " * 5
        for i in range(size):
            docs.append(base_text + f" Unique ID {i}")
        return docs

    def run_benchmark(self):
        print(f"{'SIZE':<10} | {'INDEX (s)':<15} | {'LATENCY (ms)':<15} | {'QPS':<10} | {'MEMORY (MB)':<12}")
        print("-" * 75)
        
        # Load model once
        self.model = SentenceTransformer(MODEL_NAME)
        
        for size in CORPUS_SIZES:
            self._benchmark_size(size)

        self._save_results()

    def _benchmark_size(self, size):
        latency_means = []
        throughput_means = []
        index_time_means = []
        memory_usage_means = []
        
        for i in range(ITERATIONS):
            docs = self.generate_synthetic_data(size)
            
            # Measure Pipeline
            start_mem = self.get_memory_mb()
            t0_pipeline = time.perf_counter()
            
            # Embed
            embeddings = self.model.encode(docs, batch_size=32, show_progress_bar=False, convert_to_numpy=True)
            
            # Index
            d = embeddings.shape[1]
            index = faiss.IndexFlatL2(d)
            index.add(embeddings)
            
            t1_pipeline = time.perf_counter()
            end_mem = self.get_memory_mb()
            
            index_time_means.append(t1_pipeline - t0_pipeline)
            memory_usage_means.append(end_mem - start_mem)

            # Query Benchmark
            # Warmup
            warm_q = self.model.encode(["warmup"]*WARMUP_QUERIES, convert_to_numpy=True)
            index.search(warm_q, k=5)
            
            # Actual Query
            queries = [f"query {j}" for j in range(NUM_QUERIES)]
            query_latencies = []
            
            t0_total_query = time.perf_counter()
            
            for q in queries:
                t_q_start = time.perf_counter()
                q_emb = self.model.encode([q], convert_to_numpy=True)
                index.search(q_emb, k=5)
                t_q_end = time.perf_counter()
                query_latencies.append((t_q_end - t_q_start) * 1000) # ms
            
            t1_total_query = time.perf_counter()
            
            avg_lat = np.mean(query_latencies)
            qps = NUM_QUERIES / (t1_total_query - t0_total_query)
            
            latency_means.append(avg_lat)
            throughput_means.append(qps)

        # Aggregate Results
        res = {
            "index_time": {
                "mean": np.mean(index_time_means),
                "std": np.std(index_time_means)
            },
            "memory_mb": {
                "mean": np.mean(memory_usage_means),
                "std": np.std(memory_usage_means)
            },
            "query_latency_ms": {
                "mean": np.mean(latency_means),
                "std": np.std(latency_means)
            },
            "throughput_qps": {
                "mean": np.mean(throughput_means),
                "std": np.std(throughput_means)
            }
        }
        self.results[str(size)] = res
        
        # Print Row
        print(f"{size:<10} | {res['index_time']['mean']:<15.4f} | {res['query_latency_ms']['mean']:<15.4f} | {res['throughput_qps']['mean']:<10.2f} | {res['memory_mb']['mean']:<12.2f}")

    def _save_results(self):
        # JSON
        def convert(o):
            if isinstance(o, np.generic): return o.item()
            raise TypeError

        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=4, default=convert)
        
        # CSV
        headers = ["Corpus Size", "Index Time Mean (s)", "Index Time Std", "Peak Memory Mean (MB)", "Query Latency Mean (ms)", "Query Latency Std", "Throughput Mean (QPS)"]
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for size in CORPUS_SIZES:
                r = self.results[str(size)]
                writer.writerow([
                    size,
                    f"{r['index_time']['mean']:.4f}",
                    f"{r['index_time']['std']:.4f}",
                    f"{r['memory_mb']['mean']:.4f}",
                    f"{r['query_latency_ms']['mean']:.4f}",
                    f"{r['query_latency_ms']['std']:.4f}",
                    f"{r['throughput_qps']['mean']:.4f}"
                ])
        
        print(f"\nResults saved to:\n{OUTPUT_JSON}\n{OUTPUT_CSV}")

if __name__ == "__main__":
    benchmark = ScalabilityBenchmark()
    benchmark.run_benchmark()
