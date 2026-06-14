# Artmind Benchmarking & Evaluation Framework Specification

**Version**: 1.0  
**Last Updated**: June 2026  
**Status**: Specification

## Executive Summary

This document specifies a production-grade benchmarking framework for evaluating Artmind knowledge retrieval systems operating on enterprise domain-specific schemas. The framework adapts methodologies from GraphRAG-Bench (hierarchical reasoning, complexity stratification) and SOPRAG (procedural SOP evaluation) to assess retrieval accuracy, generation quality, and procedural adherence across diverse document types in banking operations, contact center workflows, and domain-specific knowledge bases.

The framework comprises five integrated components: curated baseline datasets, structured question hierarchies, composite evaluation metrics, a Python-based test harness, and a real-time results dashboard.

---

## 1. Component Overview

### 1.1 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Artmind Evaluation Loop                    │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  [1. Datasets] ──→ [2. Questions] ──→ [3. Metrics]          │
│       ↓                 ↓                    ↓                │
│    Load Docs      Generate Q/A Pairs    Define Rubric        │
│    Index with     Stratify by Task      Setup Evaluators     │
│    Artmind        Complexity                                  │
│                                                               │
│       ↓                 ↓                    ↓                │
│       └─────────────→ [4. Python Test Harness] ←──────┘     │
│                       │                                      │
│                  ┌────┴─────────────────┐                    │
│                  │   Run Benchmark      │                    │
│                  │   • Retrieve         │                    │
│                  │   • Generate         │                    │
│                  │   • Evaluate         │                    │
│                  │   • Collect Metrics  │                    │
│                  └────┬─────────────────┘                    │
│                       │                                      │
│                  [5. Results Dashboard] ◄──── Metrics DB    │
│                  • Real-time tracking                        │
│                  • Comparative analysis                      │
│                  • Regression detection                      │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Component 1: Downloaded & Baselined Datasets

### 2.1 Dataset Taxonomy

Datasets are organized in three tiers, mirroring GraphRAG-Bench stratification:

#### 2.1.1 Tier 1: Benchmark Datasets (Public/Adapted)

| Dataset | Domain | Source | Doc Count | Focus | Notes |
|---------|--------|--------|-----------|-------|-------|
| **SOPRAG-Industrial** | Data Center Operations | SOPRAG | 414 | Procedural retrieval | Baseline for SOP evaluation |
| **GraphRAG-Bench-Medical** | Medical/Healthcare Protocols | GraphRAG-Bench | ~500 | Hierarchical schema reasoning | Domain structure reference |
| **HotpotQA-Sample** | Multi-hop reasoning | HotpotQA | 500 (stratified) | Reasoning chains | Tests cross-document inference |

#### 2.1.2 Tier 2: Domain-Specific Baseline (Custom-Curated)

Datasets curated from **anonymized** enterprise documents:

| Domain | Scope | Target Doc Count | Document Types | Schema Complexity |
|--------|-------|------------------|-----------------|-------------------|
| **Banking Operations** | KYC, AML, Account Mgmt, Dispute Resolution | 300–500 | SOPs, policies, regulatory guides, process flowcharts | High (regulatory constraints, entity relationships) |
| **Contact Center** | Call routing, escalation, complaint handling, scripting | 200–400 | Runbooks, decision trees, training guides, complaint catalogs | Medium (conditional logic, agent state) |
| **Domain X** (Customer-defined) | Domain-specific procedures | 200–500 | Domain-specific format | To be specified per domain |

**Dataset Ingestion Pipeline:**
1. **Source Documents**: Collect from KB systems, policy repositories, training materials
2. **Anonymization**: Remove PII, customer names, account numbers; replace with placeholders
3. **Normalization**: Convert to markdown, standardize headers, extract structured fields (objective, prerequisites, steps, decision logic)
4. **Schema Alignment**: Validate against domain ontology (entities, relationships, constraints)
5. **Baseline Indexing**: Ingest into Artmind with specified schema and chunking strategy
6. **Metadata Tagging**: Annotate with difficulty, source domain, primary skill/entity type

### 2.2 Dataset Structure Format

Each dataset is stored in a directory structure:

```
datasets/
├── banking_operations/
│   ├── metadata.json              # Dataset summary, schema, ingestion config
│   ├── documents/
│   │   ├── kyc_procedures.md
│   │   ├── aml_escalation.md
│   │   ├── dispute_resolution.md
│   │   └── ... (300–500 docs)
│   ├── schema/
│   │   ├── ontology.yaml          # Entity types, relationships, constraints
│   │   └── extraction_rules.yaml   # Skill-based extraction patterns
│   └── ingestion_log.json         # Artmind indexing artifacts, chunk metadata
│
├── contact_center/
│   ├── metadata.json
│   ├── documents/
│   ├── schema/
│   └── ingestion_log.json
│
└── soprag_industrial/
    ├── metadata.json
    ├── documents/
    └── ingestion_log.json
```

### 2.3 Metadata Schema (metadata.json)

```json
{
  "dataset_id": "banking_operations_v1",
  "version": "1.0",
  "domain": "banking_operations",
  "description": "KYC, AML, and dispute resolution procedures for retail banking",
  "doc_count": 350,
  "source": "anonymized_internal_kb",
  "created_date": "2026-06-01",
  "schema": {
    "entities": ["Account", "Customer", "Transaction", "Regulatory_Body", "Risk_Level"],
    "relationships": ["owns", "originates", "regulates", "escalates_to"],
    "constraints": ["KYC_verification_required", "AML_threshold_$10k"]
  },
  "ingestion_config": {
    "chunking_strategy": "semantic_overlap_500_100",
    "embedding_model": "openai_text-embedding-3-large",
    "graph_backend": "neo4j",
    "vector_store": "qdrant"
  },
  "baseline_stats": {
    "avg_doc_length_words": 1250,
    "avg_chunk_count_per_doc": 3.2,
    "total_chunks": 1120,
    "entity_count": 2847,
    "relationship_count": 5932
  }
}
```

### 2.4 Ingestion Log (ingestion_log.json)

Captures Artmind indexing artifacts for reproducibility:

```json
{
  "ingestion_date": "2026-06-01T10:15:00Z",
  "artmind_version": "1.2.1",
  "extraction_results": {
    "entities_extracted": 2847,
    "relationships_extracted": 5932,
    "extraction_quality_score": 0.87,
    "failed_extractions": 12,
    "extraction_errors_log": "ingestion_errors.log"
  },
  "indexing_stats": {
    "chunks_indexed": 1120,
    "vector_embeddings_generated": 1120,
    "graph_nodes_created": 2847,
    "graph_edges_created": 5932,
    "indexing_time_seconds": 342
  },
  "schema_alignment": {
    "entities_matched_to_ontology": 0.94,
    "relationships_matched_to_schema": 0.91,
    "constraint_violations": 8,
    "manual_review_required": true
  }
}
```

---

## 3. Component 2: Questions & Ground Truth

### 3.1 Question Taxonomy

Questions are hierarchically stratified by complexity and task type, adapting GraphRAG-Bench and SOPRAG classifications:

#### 3.1.1 Task-Type Hierarchy

| Task Type | Complexity | Reasoning Style | Artmind Skill Required | Example (Banking) |
|-----------|-----------|-----------------|----------------------|-------------------|
| **Fact Retrieval** | Low | Keyword matching + entity lookup | `retrieve_fact` | "What documents define KYC requirements?" |
| **Procedural Navigation** | Low-Medium | Linear SOP execution | `navigate_procedure` | "What are the first three steps of AML screening?" |
| **Multi-hop Reasoning** | Medium | Cross-document entity chaining | `chain_facts` | "If customer flagged in AML, what escalation path applies?" |
| **Conditional Logic** | Medium-High | Decision tree traversal with constraints | `apply_decision_logic` | "Can a transaction >$10k proceed without KYC verification? Why/why not?" |
| **Contextual Summarization** | Medium-High | Fragmented info synthesis + structural coherence | `synthesize_summary` | "Summarize the dispute resolution procedure, including all decision branches." |
| **Schema-Aware Inference** | High | Multi-entity relationship reasoning + constraint satisfaction | `complex_reasoning` | "Which risk categories require escalation to compliance, and what documentation must be collected?" |
| **Creative Procedural Inference** | High | Generative reasoning beyond retrieved content | `generate_guidance` | "A customer from high-risk jurisdiction wants account transfer. What additional due diligence applies?" |

#### 3.1.2 Difficulty Stratification

Questions are sampled or generated at three difficulty levels:

- **Easy (40%)**: Fact retrieval, single-document lookup, keyword-driven
- **Medium (40%)**: Multi-hop reasoning, procedural navigation, 2–3 document integration
- **Hard (20%)**: Complex schema reasoning, conditional logic, reasoning with constraints

### 3.2 Question Dataset Format

Questions are stored in JSONL format with ground truth:

```json
{
  "question_id": "banking_001",
  "dataset_id": "banking_operations_v1",
  "task_type": "procedural_navigation",
  "difficulty": "easy",
  "question": "What are the first three steps of the KYC verification procedure?",
  "domains": ["kyc", "customer_onboarding"],
  "entities_involved": ["Customer", "KYC_Document", "Verification_Status"],
  "relationships_required": ["undergoes_kyc", "provides_document"],
  "ground_truth": {
    "answer_type": "extractive",
    "reference_documents": ["kyc_procedures.md"],
    "expected_answer": "1. Collect identification documents from customer. 2. Verify document authenticity via issuing authority. 3. Conduct name-matching check against sanctions lists.",
    "answer_spans": [
      {"doc_id": "kyc_procedures.md", "section": "Initial Steps", "span": "Collect identification documents..."}
    ]
  },
  "evaluation_config": {
    "retrieval_k": 3,
    "answer_type": "extractive",
    "metric_focus": ["mrr", "exact_match", "f1"]
  }
}
```

### 3.3 Question Generation Strategy

#### 3.3.1 Template-Based Generation

For each domain, create question templates parameterized by entity and relationship:

**Template (Procedural Navigation):**
```
"What are the [ordinal: first/second/last] [count: number] steps of [procedure_name]?"
```

**Template (Conditional Logic):**
```
"Can a [entity_type: transaction/customer] with [attribute: value] proceed without [constraint]? Why/why not?"
```

**Template (Multi-hop Reasoning):**
```
"If [condition: event/state], what [action/outcome] applies? List all [entity_type] involved."
```

#### 3.3.2 Synthetic Question Generation

1. Extract procedural steps from documents
2. Generate factual variations (step order, conditional branches, negations)
3. Combine steps across documents to create multi-hop questions
4. Validate ground truth via schema traversal (Cypher queries on Neo4j)

### 3.4 Ground Truth Annotation

For **custom-curated datasets**, ground truth is established via:

1. **Human annotation** by domain experts (SMEs): 100% of questions reviewed
2. **Schema-based validation**: Multi-hop answers validated against ontology relationships
3. **Conflict resolution**: Disagreements resolved via SME consensus or manual review

For **public benchmarks**, ground truth is inherited from source (GraphRAG-Bench, SOPRAG).

---

## 4. Component 3: Benchmark Metrics

### 4.1 Metric Categories

Metrics are organized into **Retrieval Quality**, **Generation Quality**, and **Procedural Adherence**.

### 4.2 Retrieval Quality Metrics

#### 4.2.1 Ranking Metrics

| Metric | Formula | Threshold | Interpretation |
|--------|---------|-----------|-----------------|
| **MRR@K** (Mean Reciprocal Rank) | `1/K * Σ(1 / rank_of_first_relevant_doc)` | ≥ 0.65 | Avg position of first relevant doc (K=5, 10) |
| **Recall@K** | `relevant_docs_in_top_k / total_relevant_docs` | ≥ 0.70 | % of all relevant docs retrieved at top K |
| **Precision@K** | `relevant_docs_in_top_k / K` | ≥ 0.60 | % of top K results that are relevant |
| **NDCG@K** (Normalized DCG) | `DCG@K / ideal_DCG@K` | ≥ 0.70 | Discounted cumulative gain (penalizes poor ranking) |
| **Hit Rate@K** | `queries_with_≥1_relevant_doc_in_top_k / total_queries` | ≥ 0.75 | % of queries with ≥1 relevant doc retrieved |

#### 4.2.2 Entity & Relationship Retrieval

| Metric | Formula | Threshold | Interpretation |
|--------|---------|-----------|-----------------|
| **Entity Coverage** | `unique_relevant_entities_retrieved / total_entities_required` | ≥ 0.80 | % of required entities present in retrieved docs |
| **Relationship Recall** | `relationships_in_retrieved_context / total_relationships_in_answer` | ≥ 0.75 | % of entity relationships needed for answer |
| **Schema Alignment** | `extracted_entities_matching_ontology / total_entities_retrieved` | ≥ 0.85 | Consistency with domain schema |

### 4.3 Generation Quality Metrics

#### 4.3.1 Extractive Answer Quality (for procedural Q&A)

| Metric | Formula | Threshold | Interpretation |
|--------|---------|-----------|-----------------|
| **Exact Match (EM)** | `1 if normalized(predicted) == normalized(reference) else 0` | ≥ 0.55 | Perfect token-level match (strict) |
| **F1 Score** | `2 * (precision * recall) / (precision + recall)` | ≥ 0.65 | Harmonic mean of token overlap |
| **ROUGE-L** | Longest common subsequence F1 | ≥ 0.60 | Structural similarity (accounts for word order) |

#### 4.3.2 Abstractive Answer Quality (for synthesis/inference)

| Metric | Formula | Threshold | Interpretation |
|--------|---------|-----------|-----------------|
| **Faithfulness** | % of predicted answer supported by retrieved context | ≥ 0.85 | Factual consistency (LLM-based or human) |
| **Coherence** | Semantic coherence score (LLM-based evaluation) | ≥ 0.80 | Logical flow and clarity |
| **Groundedness** | % of claims traceable to source docs | ≥ 0.80 | Avoidance of hallucination |
| **Answer Relevance** | % of predicted answer directly addressing the question | ≥ 0.80 | Precision of generation |

#### 4.3.3 Composite Metrics

| Metric | Formula | Threshold |
|--------|---------|-----------|
| **Retrieval-Gen Score** | `0.4 * (avg_retrieval_metric) + 0.6 * (avg_generation_metric)` | ≥ 0.70 |
| **Overall QA Score** | `EM_weight * em + F1_weight * f1 + Faithfulness_weight * faithfulness` | ≥ 0.70 |

### 4.4 Procedural Adherence Metrics (SOP-Specific)

#### 4.4.1 Procedure Completeness

| Metric | Definition | Threshold |
|--------|-----------|-----------|
| **Step Coverage** | % of mandatory procedure steps mentioned in answer | ≥ 0.90 |
| **Step Ordering Correctness** | % of steps in correct sequence (or correctly marked as conditional) | ≥ 0.95 |
| **Conditional Branch Completeness** | % of decision branches identified and explained | ≥ 0.85 |

#### 4.4.2 Compliance & Constraint Adherence

| Metric | Definition | Threshold |
|--------|-----------|-----------|
| **Constraint Identification** | % of policy constraints mentioned in relevant context | ≥ 0.80 |
| **Escalation Path Accuracy** | % of escalation paths correctly identified | ≥ 0.90 |
| **Compliance Mention** | % of required compliance references included | ≥ 0.85 |

### 4.5 Metric Configuration Per Task Type

| Task Type | Primary Metrics | Secondary Metrics | Composite Weight |
|-----------|-----------------|-------------------|------------------|
| Fact Retrieval | MRR, Recall@5, Precision@5 | EM, F1 | Retrieval: 0.7, Gen: 0.3 |
| Procedural Navigation | Hit Rate, Entity Coverage | Step Coverage, EM | Retrieval: 0.6, Gen: 0.4 |
| Multi-hop Reasoning | NDCG@10, Relationship Recall | F1, Faithfulness | Retrieval: 0.5, Gen: 0.5 |
| Conditional Logic | Recall@10, Schema Alignment | Constraint ID, Coherence | Retrieval: 0.6, Gen: 0.4 |
| Summarization | Recall@K, Entity Coverage | Coherence, Faithfulness, ROUGE-L | Retrieval: 0.4, Gen: 0.6 |
| Schema-Aware Inference | NDCG, Relationship Recall | Faithfulness, Constraint ID | Retrieval: 0.5, Gen: 0.5 |
| Creative Inference | Recall@K, Relevance | Coherence, Groundedness | Retrieval: 0.3, Gen: 0.7 |

### 4.6 Evaluation Rubric (LLM-Based)

For metrics requiring semantic judgment (Faithfulness, Coherence), a structured rubric is applied:

```yaml
faithfulness_rubric:
  score_5: "All statements in answer are directly supported by retrieved context"
  score_4: ">95% of statements supported; minor unsupported elaboration"
  score_3: ">85% of statements supported; some inference beyond context"
  score_2: ">70% supported; significant unsupported claims"
  score_1: "<70% supported; mostly hallucinated"

coherence_rubric:
  score_5: "Clear logical flow; well-organized; easy to follow"
  score_4: "Good logical flow; mostly well-organized"
  score_3: "Adequate flow; some organizational issues"
  score_2: "Poor flow; significant organizational gaps"
  score_1: "Incoherent; contradictory statements"
```

---

## 5. Component 4: Python Test & Evaluation Harness

### 5.1 Architecture Overview

```
artmind_benchmark/
├── config/
│   ├── benchmark_config.yaml      # Global benchmark settings
│   └── metrics_config.yaml        # Metric definitions & thresholds
├── datasets/
│   ├── loader.py                  # Dataset loading & validation
│   └── [dataset directories]
├── questions/
│   ├── generator.py               # Question generation & loading
│   └── [question JSONL files]
├── evaluators/
│   ├── base_evaluator.py          # Abstract evaluator class
│   ├── retrieval_evaluator.py     # Retrieval metrics
│   ├── generation_evaluator.py    # Generation metrics
│   ├── procedural_evaluator.py    # SOP adherence metrics
│   └── llm_judge.py               # LLM-based evaluation
├── artmind_client.py              # Artmind API wrapper
├── benchmark_runner.py            # Main test harness orchestrator
├── results_store.py               # Metric persistence (SQLite/Postgres)
├── requirements.txt
└── main.py
```

### 5.2 Core Modules

#### 5.2.1 `benchmark_config.yaml`

```yaml
benchmark:
  name: "artmind_evaluation_v1"
  version: "1.0"
  description: "Comprehensive evaluation of Artmind KG retrieval system"
  created_date: "2026-06-01"

datasets:
  - dataset_id: "banking_operations_v1"
    domain: "banking_operations"
    enabled: true
    sample_size: null  # Use all documents; set to int for sampling
  - dataset_id: "soprag_industrial"
    domain: "sop"
    enabled: true
    sample_size: 500

questions:
  - question_set_id: "banking_001"
    dataset_id: "banking_operations_v1"
    enabled: true
    sample_size: null
    difficulty_distribution:
      easy: 0.40
      medium: 0.40
      hard: 0.20

artmind:
  base_url: "http://localhost:8000"  # or remote endpoint
  timeout_seconds: 30
  max_retries: 3
  retrieval_params:
    top_k: 10
    rerank: true
    reranker_model: "cross-encoder"

evaluation:
  retrieval_metrics: ["mrr@5", "mrr@10", "recall@5", "recall@10", "precision@5", "hit_rate@10", "entity_coverage"]
  generation_metrics: ["em", "f1", "rouge_l", "faithfulness", "coherence"]
  procedural_metrics: ["step_coverage", "constraint_identification"]
  llm_judge_model: "claude-opus-4-6"
  
results:
  database: "sqlite:///results.db"  # or postgres://user:pass@host/db
  export_formats: ["json", "csv", "html"]
  dashboard_url: "http://localhost:8080"
```

#### 5.2.2 `artmind_client.py`

```python
import requests
import json
from typing import Dict, List, Tuple

class ArtmindClient:
    """Wrapper for Artmind KB retrieval and generation API."""
    
    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
    
    def retrieve(self, query: str, dataset_id: str, top_k: int = 10, 
                 rerank: bool = True) -> Dict:
        """
        Retrieve context for a query.
        
        Returns:
        {
            "query": str,
            "retrieved_docs": [
                {
                    "rank": int,
                    "doc_id": str,
                    "chunk_id": str,
                    "score": float,
                    "content": str,
                    "entities": [str],
                    "relationships": [str]
                }
            ],
            "retrieval_time_ms": int
        }
        """
        payload = {
            "query": query,
            "dataset_id": dataset_id,
            "top_k": top_k,
            "rerank": rerank
        }
        response = requests.post(
            f"{self.base_url}/retrieve",
            json=payload,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def generate(self, query: str, retrieved_context: List[Dict], 
                 generation_params: Dict = None) -> Dict:
        """
        Generate answer using retrieved context.
        
        Returns:
        {
            "query": str,
            "answer": str,
            "answer_type": "extractive" | "abstractive",
            "source_docs": [str],
            "confidence": float,
            "generation_time_ms": int
        }
        """
        if generation_params is None:
            generation_params = {}
        
        payload = {
            "query": query,
            "context": retrieved_context,
            **generation_params
        }
        response = requests.post(
            f"{self.base_url}/generate",
            json=payload,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def retrieve_and_generate(self, query: str, dataset_id: str, 
                             top_k: int = 10) -> Tuple[Dict, Dict]:
        """Convenience method combining retrieval and generation."""
        retrieval = self.retrieve(query, dataset_id, top_k)
        generation = self.generate(query, retrieval["retrieved_docs"])
        return retrieval, generation
```

#### 5.2.3 `benchmark_runner.py`

```python
import json
import logging
from dataclasses import dataclass
from typing import List, Dict
import yaml
from tqdm import tqdm
from datetime import datetime

from artmind_client import ArtmindClient
from datasets.loader import DatasetLoader
from questions.generator import QuestionLoader
from evaluators import (
    RetrievalEvaluator,
    GenerationEvaluator,
    ProceduralEvaluator,
    LLMJudge
)
from results_store import ResultsStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class BenchmarkRun:
    run_id: str
    benchmark_name: str
    start_time: datetime
    datasets_used: List[str]
    questions_count: int
    config: Dict

class BenchmarkRunner:
    """Orchestrates the complete benchmark evaluation."""
    
    def __init__(self, config_path: str):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.artmind_client = ArtmindClient(
            base_url=self.config['artmind']['base_url'],
            timeout=self.config['artmind']['timeout_seconds']
        )
        self.results_store = ResultsStore(
            self.config['results']['database']
        )
        self.retrieval_eval = RetrievalEvaluator(self.config)
        self.generation_eval = GenerationEvaluator(self.config)
        self.procedural_eval = ProceduralEvaluator(self.config)
        self.llm_judge = LLMJudge(
            model=self.config['evaluation']['llm_judge_model']
        )
    
    def run(self) -> Dict:
        """Execute complete benchmark."""
        run_id = self._generate_run_id()
        start_time = datetime.now()
        
        logger.info(f"Starting benchmark run: {run_id}")
        
        all_results = {
            "run_id": run_id,
            "start_time": start_time.isoformat(),
            "config": self.config,
            "dataset_results": [],
            "aggregate_metrics": {}
        }
        
        # Load datasets
        dataset_loader = DatasetLoader()
        question_loader = QuestionLoader()
        
        for dataset_config in self.config['datasets']:
            if not dataset_config['enabled']:
                continue
            
            dataset = dataset_loader.load(dataset_config['dataset_id'])
            logger.info(f"Loaded dataset: {dataset_config['dataset_id']}")
            
            # Load questions for this dataset
            questions = question_loader.load(
                dataset_id=dataset_config['dataset_id'],
                sample_size=dataset_config.get('sample_size')
            )
            
            # Run evaluation on questions
            dataset_results = self._evaluate_dataset(
                dataset_config['dataset_id'],
                dataset,
                questions
            )
            all_results['dataset_results'].append(dataset_results)
        
        # Compute aggregate metrics
        all_results['aggregate_metrics'] = self._aggregate_metrics(
            all_results['dataset_results']
        )
        
        # Store results
        self.results_store.save(all_results)
        
        end_time = datetime.now()
        all_results['end_time'] = end_time.isoformat()
        all_results['duration_seconds'] = (end_time - start_time).total_seconds()
        
        logger.info(f"Benchmark complete. Results saved to {self.config['results']['database']}")
        return all_results
    
    def _evaluate_dataset(self, dataset_id: str, dataset: Dict, 
                         questions: List[Dict]) -> Dict:
        """Evaluate a single dataset."""
        logger.info(f"Evaluating {len(questions)} questions for {dataset_id}")
        
        question_results = []
        
        for question in tqdm(questions, desc=f"Evaluating {dataset_id}"):
            q_result = self._evaluate_question(
                dataset_id,
                question
            )
            question_results.append(q_result)
        
        # Aggregate metrics for this dataset
        metrics = self._compute_dataset_metrics(question_results)
        
        return {
            "dataset_id": dataset_id,
            "question_count": len(questions),
            "question_results": question_results,
            "metrics": metrics
        }
    
    def _evaluate_question(self, dataset_id: str, question: Dict) -> Dict:
        """Evaluate a single question."""
        question_id = question['question_id']
        query = question['question']
        ground_truth = question.get('ground_truth', {})
        
        # Retrieve
        try:
            retrieval_result = self.artmind_client.retrieve(
                query=query,
                dataset_id=dataset_id,
                top_k=self.config['artmind']['retrieval_params']['top_k'],
                rerank=self.config['artmind']['retrieval_params'].get('rerank', False)
            )
        except Exception as e:
            logger.error(f"Retrieval failed for {question_id}: {str(e)}")
            return {
                "question_id": question_id,
                "error": str(e),
                "status": "failed"
            }
        
        # Generate
        try:
            generation_result = self.artmind_client.generate(
                query=query,
                retrieved_context=retrieval_result['retrieved_docs']
            )
        except Exception as e:
            logger.error(f"Generation failed for {question_id}: {str(e)}")
            return {
                "question_id": question_id,
                "error": str(e),
                "status": "failed"
            }
        
        # Evaluate retrieval
        retrieval_metrics = self.retrieval_eval.evaluate(
            query=query,
            retrieved_docs=retrieval_result['retrieved_docs'],
            ground_truth=ground_truth,
            task_type=question.get('task_type')
        )
        
        # Evaluate generation
        generation_metrics = self.generation_eval.evaluate(
            predicted_answer=generation_result['answer'],
            ground_truth_answer=ground_truth.get('expected_answer'),
            retrieved_docs=retrieval_result['retrieved_docs'],
            question=query,
            task_type=question.get('task_type')
        )
        
        # Evaluate procedural adherence (if SOP)
        procedural_metrics = {}
        if question.get('task_type', '').startswith('procedural'):
            procedural_metrics = self.procedural_eval.evaluate(
                answer=generation_result['answer'],
                question=query,
                ground_truth=ground_truth,
                schema=question.get('schema_context')
            )
        
        # LLM-based judgment for semantic metrics
        llm_scores = self.llm_judge.evaluate(
            query=query,
            answer=generation_result['answer'],
            context=retrieval_result['retrieved_docs'],
            ground_truth=ground_truth
        )
        
        # Composite score
        composite = self._compute_composite_score(
            retrieval_metrics,
            generation_metrics,
            procedural_metrics,
            llm_scores,
            question.get('task_type')
        )
        
        return {
            "question_id": question_id,
            "query": query,
            "task_type": question.get('task_type'),
            "difficulty": question.get('difficulty'),
            "status": "success",
            "retrieval": {
                "retrieved_doc_count": len(retrieval_result['retrieved_docs']),
                "top_doc_score": retrieval_result['retrieved_docs'][0]['score'] if retrieval_result['retrieved_docs'] else 0,
                "metrics": retrieval_metrics
            },
            "generation": {
                "answer": generation_result['answer'],
                "answer_type": generation_result.get('answer_type'),
                "metrics": generation_metrics,
                "llm_scores": llm_scores
            },
            "procedural": procedural_metrics if procedural_metrics else None,
            "composite_score": composite
        }
    
    def _compute_composite_score(self, retrieval_metrics: Dict, 
                                generation_metrics: Dict,
                                procedural_metrics: Dict,
                                llm_scores: Dict,
                                task_type: str) -> float:
        """Compute weighted composite score based on task type."""
        # Weights from config
        config = self.config['evaluation']
        task_weights = config.get('task_type_weights', {
            'fact_retrieval': {'retrieval': 0.7, 'generation': 0.3},
            'procedural_navigation': {'retrieval': 0.6, 'generation': 0.4},
            'multi_hop_reasoning': {'retrieval': 0.5, 'generation': 0.5},
            'schema_aware_inference': {'retrieval': 0.5, 'generation': 0.5}
        })
        
        weights = task_weights.get(task_type, {'retrieval': 0.5, 'generation': 0.5})
        
        # Average metric scores
        retrieval_score = sum(retrieval_metrics.values()) / len(retrieval_metrics) if retrieval_metrics else 0
        generation_score = sum(generation_metrics.values()) / len(generation_metrics) if generation_metrics else 0
        
        composite = (
            weights['retrieval'] * retrieval_score +
            weights['generation'] * generation_score
        )
        
        return min(1.0, composite)  # Cap at 1.0
    
    def _compute_dataset_metrics(self, question_results: List[Dict]) -> Dict:
        """Aggregate metrics across all questions in a dataset."""
        metrics = {
            "total_questions": len(question_results),
            "successful_evaluations": sum(1 for r in question_results if r.get('status') == 'success'),
            "failed_evaluations": sum(1 for r in question_results if r.get('status') == 'failed'),
            "average_composite_score": sum(
                r.get('composite_score', 0) for r in question_results 
                if r.get('status') == 'success'
            ) / sum(1 for r in question_results if r.get('status') == 'success') if any(r.get('status') == 'success' for r in question_results) else 0
        }
        
        # Per-metric aggregates
        all_retrieval_metrics = {}
        all_generation_metrics = {}
        
        for result in question_results:
            if result.get('status') != 'success':
                continue
            
            for metric_name, metric_value in result['retrieval']['metrics'].items():
                if metric_name not in all_retrieval_metrics:
                    all_retrieval_metrics[metric_name] = []
                all_retrieval_metrics[metric_name].append(metric_value)
            
            for metric_name, metric_value in result['generation']['metrics'].items():
                if metric_name not in all_generation_metrics:
                    all_generation_metrics[metric_name] = []
                all_generation_metrics[metric_name].append(metric_value)
        
        metrics['retrieval_metrics'] = {
            name: sum(values) / len(values)
            for name, values in all_retrieval_metrics.items()
        }
        
        metrics['generation_metrics'] = {
            name: sum(values) / len(values)
            for name, values in all_generation_metrics.items()
        }
        
        return metrics
    
    def _aggregate_metrics(self, dataset_results: List[Dict]) -> Dict:
        """Aggregate metrics across all datasets."""
        all_composite_scores = []
        all_retrieval_metrics = {}
        all_generation_metrics = {}
        
        for dataset_result in dataset_results:
            for q_result in dataset_result['question_results']:
                if q_result.get('status') == 'success':
                    all_composite_scores.append(q_result['composite_score'])
        
        return {
            "overall_composite_score": sum(all_composite_scores) / len(all_composite_scores) if all_composite_scores else 0,
            "total_questions_evaluated": sum(
                r['question_count'] for r in dataset_results
            ),
            "total_successful": sum(
                sum(1 for q in r['question_results'] if q.get('status') == 'success')
                for r in dataset_results
            )
        }
    
    def _generate_run_id(self) -> str:
        from datetime import datetime
        return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
```

#### 5.2.4 `evaluators/retrieval_evaluator.py`

```python
from typing import List, Dict
from rank_bm25 import BM25Okapi
import numpy as np

class RetrievalEvaluator:
    """Evaluate retrieval quality against ground truth."""
    
    def __init__(self, config: Dict):
        self.config = config
    
    def evaluate(self, query: str, retrieved_docs: List[Dict], 
                 ground_truth: Dict, task_type: str = None) -> Dict:
        """Compute retrieval metrics."""
        
        # Get relevant document IDs from ground truth
        reference_docs = ground_truth.get('reference_documents', [])
        if not reference_docs:
            return {}
        
        retrieved_doc_ids = [doc['doc_id'] for doc in retrieved_docs]
        
        metrics = {}
        
        # MRR@5, MRR@10
        mrr_5 = self._compute_mrr(retrieved_doc_ids, reference_docs, k=5)
        mrr_10 = self._compute_mrr(retrieved_doc_ids, reference_docs, k=10)
        metrics['mrr@5'] = mrr_5
        metrics['mrr@10'] = mrr_10
        
        # Recall@5, Recall@10
        recall_5 = self._compute_recall(retrieved_doc_ids[:5], reference_docs)
        recall_10 = self._compute_recall(retrieved_doc_ids[:10], reference_docs)
        metrics['recall@5'] = recall_5
        metrics['recall@10'] = recall_10
        
        # Precision@5, Precision@10
        precision_5 = self._compute_precision(retrieved_doc_ids[:5], reference_docs)
        precision_10 = self._compute_precision(retrieved_doc_ids[:10], reference_docs)
        metrics['precision@5'] = precision_5
        metrics['precision@10'] = precision_10
        
        # Hit Rate@10
        hit_rate = 1.0 if any(doc_id in reference_docs for doc_id in retrieved_doc_ids[:10]) else 0.0
        metrics['hit_rate@10'] = hit_rate
        
        # Entity Coverage
        entity_coverage = self._compute_entity_coverage(
            retrieved_docs,
            ground_truth.get('entities_involved', [])
        )
        metrics['entity_coverage'] = entity_coverage
        
        return metrics
    
    def _compute_mrr(self, retrieved: List[str], relevant: List[str], k: int) -> float:
        """Mean Reciprocal Rank."""
        for i, doc_id in enumerate(retrieved[:k]):
            if doc_id in relevant:
                return 1.0 / (i + 1)
        return 0.0
    
    def _compute_recall(self, retrieved: List[str], relevant: List[str]) -> float:
        """Recall: % of relevant docs retrieved."""
        if not relevant:
            return 0.0
        return len(set(retrieved) & set(relevant)) / len(relevant)
    
    def _compute_precision(self, retrieved: List[str], relevant: List[str]) -> float:
        """Precision: % of retrieved docs that are relevant."""
        if not retrieved:
            return 0.0
        return len(set(retrieved) & set(relevant)) / len(retrieved)
    
    def _compute_entity_coverage(self, retrieved_docs: List[Dict], 
                                required_entities: List[str]) -> float:
        """% of required entities present in retrieved docs."""
        if not required_entities:
            return 1.0
        
        extracted_entities = set()
        for doc in retrieved_docs:
            extracted_entities.update(doc.get('entities', []))
        
        return len(set(required_entities) & extracted_entities) / len(required_entities)
```

#### 5.2.5 `evaluators/generation_evaluator.py`

```python
from typing import List, Dict
from rouge_score import rouge_scorer
from collections import Counter
import numpy as np

class GenerationEvaluator:
    """Evaluate answer generation quality."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.rouge_scorer = rouge_scorer.RougeScorer(['rouge1', 'rougeL'], use_stemmer=True)
    
    def evaluate(self, predicted_answer: str, ground_truth_answer: str,
                 retrieved_docs: List[Dict], question: str,
                 task_type: str = None) -> Dict:
        """Compute generation metrics."""
        
        if not ground_truth_answer:
            return {}
        
        metrics = {}
        
        # Normalize answers for comparison
        pred_norm = self._normalize(predicted_answer)
        truth_norm = self._normalize(ground_truth_answer)
        
        # Exact Match
        metrics['exact_match'] = 1.0 if pred_norm == truth_norm else 0.0
        
        # F1 Score (token overlap)
        metrics['f1'] = self._compute_f1(pred_norm, truth_norm)
        
        # ROUGE-L
        rouge_scores = self.rouge_scorer.score(truth_norm, pred_norm)
        metrics['rouge_l'] = rouge_scores['rougeL'].fmeasure
        
        return metrics
    
    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        import re
        text = text.lower()
        text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
        text = re.sub(r'[^\w\s]', '', text)  # Remove punctuation
        return text.strip()
    
    def _compute_f1(self, predicted: str, reference: str) -> float:
        """Compute F1 score based on token overlap."""
        pred_tokens = predicted.split()
        ref_tokens = reference.split()
        
        common = Counter(pred_tokens) & Counter(ref_tokens)
        num_same = sum(common.values())
        
        if num_same == 0:
            return 0.0
        
        precision = num_same / len(pred_tokens) if pred_tokens else 0.0
        recall = num_same / len(ref_tokens) if ref_tokens else 0.0
        
        if precision + recall == 0:
            return 0.0
        
        return 2 * (precision * recall) / (precision + recall)
```

#### 5.2.6 `evaluators/llm_judge.py`

```python
import json
from typing import Dict, List
import anthropic

class LLMJudge:
    """LLM-based evaluation for semantic metrics."""
    
    def __init__(self, model: str = "claude-opus-4-6"):
        self.client = anthropic.Anthropic()
        self.model = model
    
    def evaluate(self, query: str, answer: str, context: List[Dict],
                 ground_truth: Dict) -> Dict:
        """Evaluate with LLM judge."""
        
        context_str = "\n\n".join([
            f"Doc {i+1} ({doc.get('doc_id', 'unknown')}):\n{doc.get('content', '')}"
            for i, doc in enumerate(context[:3])  # Use top 3 docs
        ])
        
        prompt = f"""Evaluate the following QA response on semantic quality metrics.

Question: {query}

Retrieved Context:
{context_str}

Generated Answer:
{answer}

Expected Answer (if available):
{ground_truth.get('expected_answer', 'Not provided')}

Evaluate on:
1. Faithfulness (0-1): Is the answer supported by the retrieved context?
2. Coherence (0-1): Is the answer logically coherent and well-organized?
3. Groundedness (0-1): Are all claims traceable to the context?
4. Answer Relevance (0-1): Does the answer address the question?

Return as JSON: {{"faithfulness": float, "coherence": float, "groundedness": float, "answer_relevance": float}}"""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        try:
            scores = json.loads(response.content[0].text)
            return {
                "faithfulness": scores.get("faithfulness", 0.5),
                "coherence": scores.get("coherence", 0.5),
                "groundedness": scores.get("groundedness", 0.5),
                "answer_relevance": scores.get("answer_relevance", 0.5)
            }
        except:
            return {
                "faithfulness": 0.5,
                "coherence": 0.5,
                "groundedness": 0.5,
                "answer_relevance": 0.5
            }
```

#### 5.2.7 `main.py`

```python
import argparse
import json
from benchmark_runner import BenchmarkRunner

def main():
    parser = argparse.ArgumentParser(description="Artmind Benchmark Evaluation")
    parser.add_argument("--config", default="config/benchmark_config.yaml", 
                       help="Path to benchmark config")
    parser.add_argument("--dataset", help="Evaluate single dataset (optional)")
    parser.add_argument("--export", action="store_true", help="Export results")
    
    args = parser.parse_args()
    
    runner = BenchmarkRunner(args.config)
    results = runner.run()
    
    print(json.dumps(results['aggregate_metrics'], indent=2))
    
    if args.export:
        import csv
        # Export to CSV for dashboard ingestion
        pass

if __name__ == "__main__":
    main()
```

### 5.3 Running the Benchmark

```bash
# Install dependencies
pip install -r requirements.txt

# Run full benchmark
python main.py --config config/benchmark_config.yaml

# Run single dataset
python main.py --config config/benchmark_config.yaml --dataset banking_operations_v1

# Export results
python main.py --config config/benchmark_config.yaml --export
```

---

## 6. Component 5: Results Dashboard

### 6.1 Dashboard Architecture

```
Frontend: React + Plotly/Recharts
├── Real-time Metrics Display
├── Comparative Analysis
├── Regression Detection
├── Task Type Breakdown
└── Question-Level Drill-Down

Backend: FastAPI
├── Results API (/api/results)
├── Comparison API (/api/compare)
├── Export API (/api/export)
└── WebSocket for live updates

Data: SQLite/Postgres
└── Results table with full QA artifacts
```

### 6.2 Dashboard Components

#### 6.2.1 Overview Dashboard

**Metrics Display:**
- **Overall Composite Score**: Large badge showing avg score (threshold: ≥0.70)
- **Success Rate**: % of successful evaluations
- **Total Questions**: Count by difficulty level
- **Time Series**: Composite score trend across runs

**Key Metrics Table:**
```
| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Composite Score | 0.72 | 0.70 | ✓ Pass |
| MRR@10 | 0.68 | 0.65 | ✓ Pass |
| F1 | 0.63 | 0.65 | ✗ Fail |
| Faithfulness | 0.82 | 0.85 | ✗ Fail |
| Step Coverage | 0.91 | 0.90 | ✓ Pass |
```

#### 6.2.2 Task Type Breakdown

- **Composite Score by Task Type** (bar chart)
- **Metric Performance Heatmap** (per task type vs metric)
- **Difficulty Distribution** (pie chart of easy/medium/hard)

#### 6.2.3 Comparative Analysis

- **Run-over-run trends** (line chart)
- **A/B comparison** (side-by-side metric comparison)
- **Dataset comparison** (performance across datasets)

#### 6.2.4 Question-Level Drill-Down

- **Sortable table** of all questions with results
- **Filter by**: task type, difficulty, pass/fail, dataset
- **Expand row** to see:
  - Retrieved documents with scores
  - Generated answer
  - Ground truth
  - Individual metrics
  - LLM judge scores

#### 6.2.5 Regression Detection

- **Alert system**: Highlight ≥5% drop in key metrics
- **Affected questions**: List questions causing regression
- **Root cause analysis**: Retrieve vs generation component analysis

### 6.3 Dashboard API Endpoints

```python
# FastAPI backend

@app.get("/api/results/{run_id}")
async def get_run_results(run_id: str):
    """Fetch complete results for a run."""
    return results_store.get_run(run_id)

@app.get("/api/results/{run_id}/metrics")
async def get_run_metrics(run_id: str):
    """Fetch aggregated metrics."""
    return results_store.get_metrics(run_id)

@app.get("/api/results/{run_id}/questions")
async def get_questions(run_id: str, skip: int = 0, limit: int = 50,
                       task_type: str = None, difficulty: str = None,
                       status: str = None):
    """Fetch paginated question results with filters."""
    return results_store.get_questions(
        run_id, skip, limit, 
        filters={'task_type': task_type, 'difficulty': difficulty, 'status': status}
    )

@app.get("/api/results/{run_id}/questions/{question_id}")
async def get_question_detail(run_id: str, question_id: str):
    """Fetch full question result with all artifacts."""
    return results_store.get_question_detail(run_id, question_id)

@app.post("/api/compare")
async def compare_runs(run_ids: List[str]):
    """Compare metrics across multiple runs."""
    results = [results_store.get_metrics(rid) for rid in run_ids]
    return compute_comparison(results)

@app.get("/api/results/{run_id}/export")
async def export_results(run_id: str, format: str = "json"):
    """Export results in specified format."""
    # json, csv, html
    return results_store.export(run_id, format)

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    """Live updates during benchmark execution."""
    await websocket.accept()
    # Stream updates as they complete
```

### 6.4 Dashboard UI Components

**Main View:**
```
[Run Selector dropdown] [Compare runs] [Export]
┌─────────────────────────────────────────────────────┐
│ OVERALL COMPOSITE SCORE: 0.72 ✓ PASS               │
├─────────────────────────────────────────────────────┤
│ Success Rate: 98%  |  Total: 250 questions          │
│ Avg Time/Q: 2.3s  |  Total Duration: 9m 36s         │
├─────────────────────────────────────────────────────┤
│                                                      │
│  [Retrieval] [Generation] [Procedural]             │
│  MRR@10: 0.68  |  F1: 0.63  |  Step Coverage: 0.91  │
│  Recall: 0.72  |  EM: 0.58  |  Constraint ID: 0.78  │
│                                                      │
├─────────────────────────────────────────────────────┤
│ Task Type Performance         │ Difficulty Dist.     │
│ [Bar chart]                   │ [Pie: 40% easy,      │
│                               │  40% medium,         │
│                               │  20% hard]           │
└─────────────────────────────────────────────────────┘

[Detailed Results Table with sorting/filtering]
```

---

## 7. Integration & Execution Flow

### 7.1 End-to-End Workflow

```
1. Prepare Datasets
   ├── Download from sources
   ├── Anonymize & normalize
   ├── Create metadata.json
   └── Ingest into Artmind
        └── Generate ingestion_log.json

2. Generate/Load Questions
   ├── Load question JSONL files
   ├── Stratify by difficulty
   ├── Validate ground truth
   └── Sample if needed

3. Run Benchmark
   ├── Initialize ArtmindClient
   ├── For each question:
   │   ├── Call artmind.retrieve()
   │   ├── Call artmind.generate()
   │   ├── Evaluate with RetrievalEvaluator
   │   ├── Evaluate with GenerationEvaluator
   │   ├── Query LLMJudge for semantic scores
   │   └── Compute composite score
   └── Aggregate dataset metrics

4. Store Results
   ├── Persist to SQLite/Postgres
   ├── Export JSON snapshot
   └── Trigger webhook to dashboard

5. View Dashboard
   ├── Load latest run
   ├── Compare with previous runs
   ├── Drill-down on failing questions
   └── Export for reporting
```

### 7.2 CI/CD Integration

```yaml
# .github/workflows/benchmark.yml

name: Artmind Benchmark

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly
  workflow_dispatch:

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run benchmark
        run: python main.py --config config/benchmark_config.yaml --export
      - name: Upload results
        uses: actions/upload-artifact@v3
        with:
          name: benchmark-results
          path: results/
      - name: Comment on PR
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v6
        with:
          script: |
            // Post summary to PR
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: readResults()
            })
```

---

## 8. Metrics Thresholds & SLOs

### 8.1 Per-Component Thresholds

| Component | Metric | Good | Warning | Critical |
|-----------|--------|------|---------|----------|
| **Retrieval** | MRR@10 | ≥0.70 | 0.65–0.70 | <0.65 |
| | Recall@10 | ≥0.75 | 0.70–0.75 | <0.70 |
| | Hit Rate@10 | ≥0.80 | 0.75–0.80 | <0.75 |
| **Generation** | F1 | ≥0.70 | 0.65–0.70 | <0.65 |
| | EM | ≥0.60 | 0.55–0.60 | <0.55 |
| | Faithfulness | ≥0.85 | 0.80–0.85 | <0.80 |
| **Procedural** | Step Coverage | ≥0.90 | 0.85–0.90 | <0.85 |
| | Constraint ID | ≥0.80 | 0.75–0.80 | <0.75 |
| **Composite** | Overall Score | ≥0.70 | 0.65–0.70 | <0.65 |

### 8.2 Success Criteria

- **Pass**: All component thresholds in "Good" range
- **Conditional Pass**: ≥80% of metrics in "Good" range, others in "Warning"
- **Fail**: Any metric in "Critical" range, or >20% metrics in "Warning"

---

## 9. Appendix A: Configuration Reference

### Metric Configuration (metrics_config.yaml)

```yaml
metrics:
  retrieval:
    mrr@5:
      type: ranking
      lower_bound: 0
      upper_bound: 1
      threshold: 0.65
      weight: 0.2
    recall@10:
      type: ranking
      threshold: 0.70
      weight: 0.2
    hit_rate@10:
      type: binary
      threshold: 0.75
      weight: 0.15
  
  generation:
    f1:
      type: token_overlap
      threshold: 0.65
      weight: 0.25
    exact_match:
      type: binary
      threshold: 0.55
      weight: 0.15
    faithfulness:
      type: semantic
      evaluator: llm_judge
      threshold: 0.85
      weight: 0.20
  
  procedural:
    step_coverage:
      type: entity_coverage
      threshold: 0.90
      weight: 0.15
    constraint_identification:
      type: recall
      threshold: 0.80
      weight: 0.10
```

---

## 10. Implementation Roadmap

### Phase 1 (Weeks 1–2)
- [ ] Implement Component 1: Dataset infrastructure
- [ ] Create banking_operations baseline dataset
- [ ] Write dataset loader and validator

### Phase 2 (Weeks 3–4)
- [ ] Implement Component 2: Question generation & loading
- [ ] Create 250–300 questions across task types
- [ ] Ground truth annotation

### Phase 3 (Weeks 5–6)
- [ ] Implement Component 3: Metrics definitions
- [ ] Build evaluators (retrieval, generation, procedural)
- [ ] Integrate LLM judge

### Phase 4 (Weeks 7–8)
- [ ] Implement Component 4: Python test harness
- [ ] Artmind client integration
- [ ] Results storage & persistence

### Phase 5 (Weeks 9–10)
- [ ] Implement Component 5: Dashboard
- [ ] FastAPI backend
- [ ] React frontend

### Phase 6 (Week 11+)
- [ ] Adapt SOPRAG dataset
- [ ] Add contact center domain
- [ ] CI/CD pipeline
- [ ] Documentation & training

---

## 11. Success Criteria

The framework is considered production-ready when:

1. ✓ All 5 components fully implemented and integrated
2. ✓ ≥250 questions across 3+ task types
3. ✓ Benchmark runs complete in <15 minutes
4. ✓ Dashboard displays real-time results with <2s latency
5. ✓ Composite score ≥0.70 on baseline datasets
6. ✓ Regression detection alerts functioning
7. ✓ Weekly automated runs via CI/CD
8. ✓ API fully documented with examples
9. ✓ Training materials & runbooks complete

---

**Document Version**: 1.0  
**Last Reviewed**: June 2026  
**Next Review**: September 2026
