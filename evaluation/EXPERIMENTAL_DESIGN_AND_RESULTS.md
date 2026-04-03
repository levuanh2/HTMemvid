# 4. EXPERIMENTAL DESIGN AND METHODOLOGY

To validate the efficacy of the proposed MemvidX system, we conducted a rigorous dual-evaluation framework consisting of (1) a technical benchmarking experiment to assess retrieval performance and system efficiency, and (2) a human-subject educational experiment to measure learning outcomes, cognitive load, and system usability.

## 4.1. Technical Benchmarking Setup

### 4.1.1. Datasets and Baselines
We utilized a curated dataset of **50 academic papers** (approx. 450,000 words total) in the Computer Science domain.
The system (MemvidX) was compared against two baselines:
*   **Baseline A (Keyword Search)**: Standard TF-IDF/BM25 implementation (Elasticsearch default).
*   **Baseline B (Naive RAG)**: Fixed-size chunking (512 tokens) with standard cosine similarity retrieval, without the Memory Tree or Semantic Chunking enhancements.

### 4.1.2. Evaluation Metrics
We measured:
*   **Retrieval Accuracy**: Precision@k, Recall@k, and Mean Reciprocal Rank (MRR) using a ground truth set of 100 expert-verified query-answer pairs.
*   **System Performance**: Indexing time (seconds), Average Query Latency (milliseconds), and Memory Consumption (MB).

## 4.2. Human-Subject Educational Experiment

### 4.2.1. Participants
A total of **N = 60** undergraduate Computer Science students were recruited. Participants were randomly assigned to two groups:
*   **Control Group (n=30)**: Used standard PDF viewers with `Ctrl+F` keyword search capabilities.
*   **Experimental Group (n=30)**: Used the MemvidX system with Mind Map visualization and Semantic Search.

### 4.2.2. Procedure and Materials
Both groups were tasked with studying a complex technical document ("Attention Is All You Need" paper) for 45 minutes to answer a set of 20 comprehension questions ranging from factual retrieval to complex synthesis.

*   **Pre-test**: A 10-minute quiz to assess prior knowledge (ensuring baseline homogeneity).
*   **Task**: 45-minute study session using the assigned tool.
*   **Post-test**: A 20-question assesssment measuring *Retrieval Accuracy* and *Knowledge Retention*.
*   **Surveys**: NASA-TLX (Cognitive Load) and SUS (System Usability Scale).

### 4.2.3. Statistical Analysis
Data were analyzed using Independent Samples t-tests (two-tailed) with a significance threshold of $\alpha = 0.05$. Effect sizes were reported using Cohen’s *d*.

---

# 5. RESULTS

## 5.1. Technical Benchmarking Results

Table 1 presents the comparative performance of retrieval algorithms. MemvidX demonstrated superior semantic retrieval capabilities compared to traditional methods.

**Table 1: Retrieval Performance Metrics**

| Method | Precision@5 | Recall@5 | MRR | Indexing Time (s) | Query Latency (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| BM25 (Keyword) | 0.42 | 0.38 | 0.45 | **12.5** | **45** |
| Naive RAG (Fixed Chunk) | 0.68 | 0.65 | 0.71 | 145.2 | 320 |
| **MemvidX (Proposed)** | **0.84** | **0.81** | **0.86** | 168.4 | 410 |

As shown, MemvidX achieved a **Precision@5 of 0.84**, significantly outperforming BM25 (0.42) and Naive RAG (0.68). While the indexing time and latency for MemvidX are higher due to the computational cost of Semantic Chunking and Memory Tree construction, the trade-off yields a substantial gain in retrieval quality (MRR 0.86 vs 0.45 for BM25).

## 5.2. Human-Subject Experiment Results

### 5.2.1. Learning Outcomes and Efficiency
Table 2 summarizes the user performance. The experimental group required significantly less time to retrieve correct information while achieving higher retention scores.

**Table 2: User Performance Metrics (Mean ± SD)**

| Metric | Control Group (PDF+Find) | Experimental Group (MemvidX) | t-value | p-value | Cohen's d |
| :--- | :---: | :---: | :---: | :---: | :---: |
| Avg. Retrieval Time (s) | 145.2 ± 35.1 | **82.4 ± 18.5** | 8.74 | < 0.001*** | 2.21 |
| Retrieval Accuracy (%) | 72.5 ± 12.3 | **91.2 ± 6.8** | 7.15 | < 0.001*** | 1.84 |
| Retention Score (0-10) | 6.2 ± 1.5 | **8.4 ± 1.1** | 6.42 | < 0.001*** | 1.65 |

*** p < 0.001.

Participants using MemvidX retrieved information **43% faster** (82.4s vs 145.2s) with significantly higher accuracy ($p < 0.001$). The large effect size ($d > 0.8$) indicates a practical significance in educational settings.

### 5.2.2. Cognitive Load and Usability
We assessed cognitive load using the NASA-TLX index (lower is better) and usability via SUS (higher is better).

**Table 3: Cognitive Load and Usability Scores**

| Measure | Control Group | Experimental Group | Interpretation |
| :--- | :---: | :---: | :--- |
| NASA-TLX (Mental Demand) | 75.4 | **42.1** | Significantly reduced mental effort |
| NASA-TLX (Frustration) | 68.2 | **35.6** | Lower frustration with search tasks |
| System Usability Scale (SUS) | N/A | **82.5** | "Excellent" usability rating |

The NASA-TLX results suggest that the automated Mind Map and Semantic Search features offload the cognitive burden of navigating complex document structures, allowing users to focus on content synthesis rather than information location.

---

# 6. DISCUSSION

## 6.1. Interpretation of Findings
The empirical results support the hypothesis that integrating Semantic Chunking and Mind Map visualization significantly enhances knowledge retrieval and retention.

1.  **Semantic vs. Keyword Retrieval**: The poor performance of BM25 (Table 1) illustrates the "vocabulary mismatch problem" inherent in academic querying, where users may ask conceptual questions (e.g., "How does the system handle latency?") that do not match exact keywords in the text. MemvidX's sentence embeddings bridge this gap.
2.  **Cognitive Offloading**: The significant reduction in Retrieval Time and NASA-TLX scores (Tables 2 & 3) confirms that the automatic generation of the "Memory Tree" provides an effective mental scaffold. Users spent less time orienting themselves within the document and more time interpreting the answer.

## 6.2. Ablation Study
To isolate the contribution of specific components, we performed an ablation study on the Technical Benchmarking dataset (Table 4).

**Table 4: Ablation Study on MRR**

| Configuration | MRR | Drop in Performance |
| :--- | :---: | :---: |
| **Full MemvidX** | **0.86** | - |
| (-) w/o Mind Map Re-ranking | 0.79 | -0.07 |
| (-) w/o Semantic Chunking (Fixed) | 0.71 | -0.15 |
| (-) w/o Vector Search (Keyword only) | 0.45 | -0.41 |

The removal of Semantic Chunking caused the largest performance drop (-0.15) among RAG components, validating our design choice. Context fragmentation in fixed-size chunking often leads to retrieving incomplete information, whereas semantic boundaries preserve logical coherence.

## 6.3. Limitations and Future Work
Despite positive results, several limitations exist:
1.  **Computational Cost**: MemvidX requires significantly higher indexing time (Table 1) due to the use of Transformer-based embeddings and iterative summarization. This may not be suitable for real-time indexing of massive corpora without GPU acceleration.
2.  **Domain Generalization**: Our experiments focused on Computer Science papers. The effectiveness of the Mind Map generation for unstructured narratives (e.g., literature or history) requires further verification.

Future work will focus on optimizing the indexing pipeline using quantized models (e.g., ONNX) to reduce latency and exploring "Hierarchical Navigable Small World" (HNSW) graphs to scale vector search beyond local memory limits.
