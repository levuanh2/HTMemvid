
import os
import time
import json
import psutil
import numpy as np
import scipy.stats
import faiss
import multiprocessing
import tempfile
from sentence_transformers import SentenceTransformer
from typing import Dict, Any, List

# Configuration
BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(BENCHMARK_DIR, "scalability_clean_memory_results.json")

MODEL_NAME = "all-MiniLM-L6-v2"
CORPUS_SIZES = [100, 500, 1000, 2000]
NUM_QUERIES = 100
ITERATIONS = 5

def generate_synthetic_data(size):
    """Generate synthetic documents."""
    docs = []
    base_text = "Machine learning (ML) is a field of inquiry devoted to understanding and building methods that 'learn', that is, methods that leverage data to improve performance on some set of tasks. " * 5
    for i in range(size):
        docs.append(base_text + f" Unique ID {i}")
    return docs

def get_memory_mb():
    """Get current process memory usage (RSS) in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)

def worker_benchmark(corpus_size, result_queue):
    """Worker process to run a single benchmark iteration in isolation."""
    metrics = {}
    
    try:
        # Measure Initial Memory
        mem_baseline = get_memory_mb()
        
        # 1. Model Loading Memory
        model = SentenceTransformer(MODEL_NAME)
        mem_after_model = get_memory_mb()
        metrics['model_memory_mb'] = mem_after_model - mem_baseline
        
        # Generate Data
        docs = generate_synthetic_data(corpus_size)
        
        # 2. Embedding Memory & Time
        t0_embed = time.perf_counter()
        embeddings = model.encode(docs, batch_size=32, show_progress_bar=False, convert_to_numpy=True)
        t1_embed = time.perf_counter()
        
        # Measure physical embedding memory using nbytes
        metrics['embedding_memory_mb'] = embeddings.nbytes / (1024 * 1024)
        metrics['embed_time_seconds'] = t1_embed - t0_embed

        # 3. Indexing Memory & Time
        d = embeddings.shape[1]
        t0_index = time.perf_counter()
        index = faiss.IndexFlatL2(d)
        index.add(embeddings)
        t1_index = time.perf_counter()
        
        metrics['index_time_seconds'] = t1_index - t0_index
        
        # Measure Index Memory (serialize to bytes)
        # Using a temporary file or in-memory buffer approach for FAISS is tricky.
        # But for IndexFlatL2, logical size is vector data + overhead.
        # Let's use `faiss.serialize_index` and measure byte size.
        # Note: This is memory copy, but accurate for size on disk/RAM footprint.
        idx_bytes = faiss.serialize_index(index)
        metrics['index_memory_mb'] = idx_bytes.nbytes / (1024 * 1024)

        # Total Controlled Memory
        metrics['total_controlled_memory_mb'] = (
            metrics['model_memory_mb'] + 
            metrics['embedding_memory_mb'] + 
            metrics['index_memory_mb']
        )

        # 4. Query Benchmark
        # Warmup
        warm_q = model.encode(["warmup"]*10, convert_to_numpy=True)
        index.search(warm_q, k=5)
        
        queries = [f"query {j}" for j in range(NUM_QUERIES)]
        query_latencies = []
        
        t0_total_q = time.perf_counter()
        for q in queries:
            t_q_start = time.perf_counter()
            q_emb = model.encode([q], convert_to_numpy=True)
            index.search(q_emb, k=5)
            t_q_end = time.perf_counter()
            query_latencies.append((t_q_end - t_q_start) * 1000) # ms
        t1_total_q = time.perf_counter()
        
        metrics['query_latency_mean_ms'] = np.mean(query_latencies)
        metrics['throughput_qps'] = NUM_QUERIES / (t1_total_q - t0_total_q)
        
        result_queue.put(metrics)
        
    except Exception as e:
        result_queue.put({"error": str(e)})

class ScalabilityCleanBenchmark:
    def __init__(self):
        self.aggregated_results = {}

    def calculate_stats(self, values):
        """Calculate Mean, Std, 95% CI."""
        n = len(values)
        if n < 2:
            return np.mean(values), 0.0, 0.0
        
        mean = np.mean(values)
        std = np.std(values, ddof=1)
        se = std / np.sqrt(n)
        # 95% Confidence Interval using t-distribution
        ci = se * scipy.stats.t.ppf((1 + 0.95) / 2., n-1)
        return mean, std, ci

    def run(self):
        print(f"Starting Clean Memory Benchmark ({ITERATIONS} iterations per size)...")
        
        for size in CORPUS_SIZES:
            print(f"Benchmarking Corpus Size: {size}")
            results_for_size = []
            
            for i in range(ITERATIONS):
                queue = multiprocessing.Queue()
                p = multiprocessing.Process(target=worker_benchmark, args=(size, queue))
                p.start()
                metric = queue.get()
                p.join()
                
                if "error" in metric:
                    print(f"Error in iteration {i}: {metric['error']}")
                    continue
                
                results_for_size.append(metric)
                # print(f"  Iter {i+1}: Latency {metric['query_latency_mean_ms']:.2f}ms, Mem {metric['total_controlled_memory_mb']:.2f}MB")

            if not results_for_size:
                continue

            # Aggregate
            agg = {}
            keys = results_for_size[0].keys()
            for k in keys:
                vals = [r[k] for r in results_for_size]
                mean, std, ci = self.calculate_stats(vals)
                agg[k] = {
                    "mean": mean,
                    "std": std,
                    "ci_95": ci
                }
            self.aggregated_results[str(size)] = agg

        self.save_results()

    def save_results(self):
        # Convert numpy types
        def convert(o):
            if isinstance(o, np.generic): return o.item()
            raise TypeError

        with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
            json.dump(self.aggregated_results, f, indent=4, default=convert)
        
        print(f"\nBenchmark Complete. Results saved to: {OUTPUT_JSON}")

if __name__ == "__main__":
    # Ensure multiprocessing works correctly on Windows (spawn)
    multiprocessing.freeze_support()
    benchmark = ScalabilityCleanBenchmark()
    benchmark.run()
