import os
import time
import numpy as np
import faiss
import json
import csv
from typing import List, Dict, Tuple
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from scipy import stats

# Import local modules
import metrics
from utils import load_json, get_memory_usage_mb, Timer

# Configuration
DOCS_FILE = os.path.join(os.path.dirname(__file__), "documents.json")
QUERIES_FILE = os.path.join(os.path.dirname(__file__), "queries.json")
RESULTS_CSV = os.path.join(os.path.dirname(__file__), "benchmark_results.csv")
RESULTS_JSON = os.path.join(os.path.dirname(__file__), "benchmark_results.json")
MODEL_NAME = "all-MiniLM-L6-v2"
K_VALUES = [1, 3, 5]

class BenchmarkRunner:
    def __init__(self, docs: List[Dict], queries: List[Dict]):
        self.docs = docs
        self.queries = queries
        self.doc_texts = [d['text'] for d in self.docs]
        self.doc_ids = [d['id'] for d in self.docs]
        self.doc_id_to_idx = {d_id: i for i, d_id in enumerate(self.doc_ids)}

    def run_dense_retrieval(self) -> Dict:
        """Runs Dense Retrieval (Sentence-BERT + FAISS)."""
        print("\n--- Running Dense Retrieval (FAISS) ---")
        
        # 1. Indexing
        start_mem = get_memory_usage_mb()
        with Timer() as t:
            model = SentenceTransformer(MODEL_NAME)
            embeddings = model.encode(self.doc_texts, convert_to_numpy=True)
            dimension = embeddings.shape[1]
            index = faiss.IndexFlatL2(dimension)
            index.add(embeddings)
        
        indexing_time = t.interval
        memory_usage = get_memory_usage_mb() - start_mem
        print(f"Indexing completed in {indexing_time:.4f}s. Memory used: {memory_usage:.2f} MB")

        # 2. Querying
        results = {f"P@{k}": [] for k in K_VALUES}
        results["MRR"] = []
        results["Latency"] = []

        for q_item in self.queries:
            query_text = q_item['query']
            relevant_docs = q_item['relevant_docs']

            with Timer() as t:
                q_emb = model.encode([query_text], convert_to_numpy=True)
                distances, indices = index.search(q_emb, k=max(K_VALUES))
            
            latencies = t.interval * 1000 # ms
            results["Latency"].append(latencies)
            
            # Map indices back to doc IDs
            retrieved_ids = []
            for idx in indices[0]:
                if idx < len(self.doc_ids) and idx >= 0:
                     retrieved_ids.append(self.doc_ids[idx])
            
            for k in K_VALUES:
                p_k = metrics.calculate_precision_at_k(retrieved_ids, relevant_docs, k)
                results[f"P@{k}"].append(p_k)
            
            mrr = metrics.calculate_mrr(retrieved_ids, relevant_docs)
            results["MRR"].append(mrr)

        summary = {
            "Method": "Dense Retrieval (FAISS)",
            "Indexing Time (s)": indexing_time,
            "Memory Usage (MB)": memory_usage,
            "Raw": results
        }
        
        # Calculate Mean +/- Std
        for key, vals in results.items():
            summary[f"{key}_Mean"] = np.mean(vals)
            summary[f"{key}_Std"] = np.std(vals)
            
        return summary

    def run_sparse_retrieval(self) -> Dict:
        """Runs Sparse Retrieval (TF-IDF + Cosine Similarity)."""
        print("\n--- Running Sparse Retrieval (TF-IDF) ---")

        # 1. Indexing
        start_mem = get_memory_usage_mb()
        with Timer() as t:
            vectorizer = TfidfVectorizer()
            tfidf_matrix = vectorizer.fit_transform(self.doc_texts)
        
        indexing_time = t.interval
        memory_usage = get_memory_usage_mb() - start_mem
        print(f"Indexing completed in {indexing_time:.4f}s. Memory used: {memory_usage:.2f} MB")

        # 2. Querying
        results = {f"P@{k}": [] for k in K_VALUES}
        results["MRR"] = []
        results["Latency"] = []

        for q_item in self.queries:
            query_text = q_item['query']
            relevant_docs = q_item['relevant_docs']

            with Timer() as t:
                query_vec = vectorizer.transform([query_text])
                similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()
                # Get top K indices sorted by score descending
                top_indices = similarities.argsort()[::-1][:max(K_VALUES)]
            
            latencies = t.interval * 1000 # ms
            results["Latency"].append(latencies)

            retrieved_ids = [self.doc_ids[idx] for idx in top_indices]

            for k in K_VALUES:
                p_k = metrics.calculate_precision_at_k(retrieved_ids, relevant_docs, k)
                results[f"P@{k}"].append(p_k)

            mrr = metrics.calculate_mrr(retrieved_ids, relevant_docs)
            results["MRR"].append(mrr)

        summary = {
            "Method": "Sparse Retrieval (TF-IDF)",
            "Indexing Time (s)": indexing_time,
            "Memory Usage (MB)": memory_usage,
            "Raw": results
        }

        # Calculate Mean +/- Std
        for key, vals in results.items():
             summary[f"{key}_Mean"] = np.mean(vals)
             summary[f"{key}_Std"] = np.std(vals)

        return summary

    def perform_statistical_tests(self, dense: Dict, sparse: Dict) -> Dict:
        """Calculates Paired T-Test p-values for metrics."""
        stats_results = {}
        metrics_to_test = [f"P@{k}" for k in K_VALUES] + ["MRR", "Latency"]
        
        for metric in metrics_to_test:
            dense_vals = dense["Raw"][metric]
            sparse_vals = sparse["Raw"][metric]
            
            # Paired T-Test
            t_stat, p_val = stats.ttest_rel(dense_vals, sparse_vals)
            stats_results[metric] = {
                "t_stat": t_stat,
                "p_value": p_val,
                "significant": p_val < 0.05
            }
        return stats_results

    def save_results(self, dense_res, sparse_res, stats_res):
        # Flatten for CSV
        rows = []
        for res in [dense_res, sparse_res]:
            row = {
                "Method": res["Method"],
                "Indexing Time (s)": res["Indexing Time (s)"],
                "Memory Usage (MB)": res["Memory Usage (MB)"],
            }
            for k in K_VALUES:
                row[f"P@{k}"] = f"{res[f'P@{k}_Mean']:.4f} ± {res[f'P@{k}_Std']:.4f}"
            row["MRR"] = f"{res['MRR_Mean']:.4f} ± {res['MRR_Std']:.4f}"
            row["Latency (ms)"] = f"{res['Latency_Mean']:.2f} ± {res['Latency_Std']:.2f}"
            rows.append(row)

        # JSON dump (full data)
        full_data = {
            "dense": dense_res,
            "sparse": sparse_res,
            "statistics": stats_res
        }
        # Remove raw arrays for clean JSON if needed, but useful for debug. 
        # Making simple serializable version for JSON
        
        def convert_numpy(obj):
            if isinstance(obj, np.generic): return obj.item()
            if isinstance(obj, np.ndarray): return obj.tolist()
            return obj

        with open(RESULTS_JSON, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, indent=4, default=convert_numpy)

        # CSV
        fieldnames = ["Method", "Indexing Time (s)", "Memory Usage (MB)"] + [f"P@{k}" for k in K_VALUES] + ["MRR", "Latency (ms)"]
        with open(RESULTS_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            
            # Append stats
            f.write("\nStatistical Significance (Paired T-Test vs Baseline),\n")
            f.write("Metric,T-Statistic,P-Value,Significant (alpha=0.05)\n")
            for metric, res in stats_res.items():
                f.write(f"{metric},{res['t_stat']:.4f},{res['p_value']:.4e},{res['significant']}\n")

        print(f"\nResults saved to {RESULTS_CSV} and {RESULTS_JSON}")

    def print_summary(self, dense_res, sparse_res, stats_res):
        print("\n" + "="*100)
        print(f"{'Metric':<20} | {'Dense (FAISS)':<30} | {'Sparse (TF-IDF)':<30} | {'P-Value':<10}")
        print("-" * 100)
        
        metrics_to_show = [f"P@{k}" for k in K_VALUES] + ["MRR", "Latency"]
        
        for metric in metrics_to_show:
            if metric == "Latency":
                dense_str = f"{dense_res[f'{metric}_Mean']:.2f} ± {dense_res[f'{metric}_Std']:.2f}"
                sparse_str = f"{sparse_res[f'{metric}_Mean']:.2f} ± {sparse_res[f'{metric}_Std']:.2f}"
            else:
                dense_str = f"{dense_res[f'{metric}_Mean']:.4f} ± {dense_res[f'{metric}_Std']:.4f}"
                sparse_str = f"{sparse_res[f'{metric}_Mean']:.4f} ± {sparse_res[f'{metric}_Std']:.4f}"
            
            p_val = stats_res[metric]['p_value']
            print(f"{metric:<20} | {dense_str:<30} | {sparse_str:<30} | {p_val:.4e}")
            
        print("="*100 + "\n")

def main():
    if not os.path.exists(DOCS_FILE) or not os.path.exists(QUERIES_FILE):
        print("Error: documents.json or queries.json not found in benchmark directory.")
        return

    docs = load_json(DOCS_FILE)
    queries = load_json(QUERIES_FILE)

    print(f"Loaded {len(docs)} documents and {len(queries)} queries.")

    runner = BenchmarkRunner(docs, queries)
    
    dense_results = runner.run_dense_retrieval()
    sparse_results = runner.run_sparse_retrieval()
    stats_results = runner.perform_statistical_tests(dense_results, sparse_results)

    runner.save_results(dense_results, sparse_results, stats_results)
    runner.print_summary(dense_results, sparse_results, stats_results)

if __name__ == "__main__":
    main()

