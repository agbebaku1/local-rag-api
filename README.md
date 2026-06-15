# Private RAG Platform on Kubernetes

A privacy-first Retrieval-Augmented Generation (RAG) platform for querying private documents without sending data to external AI providers.

Built with FastAPI, ChromaDB, Ollama, Llama 3, LangGraph, and Kubernetes.

---

## Overview

This project enables organizations to upload internal documents and ask natural language questions against them using a locally hosted LLM.

Document flow:

PDF/TXT Upload
↓
FastAPI
↓
Chunking
↓
Embeddings (nomic-embed-text)
↓
ChromaDB
↓
Semantic Search
↓
Llama 3 (Ollama)
↓
Answer

All inference, embeddings, retrieval, and storage remain inside the Kubernetes cluster.

Potential use cases:

* Legal document search
* Internal policy assistants
* Compliance and regulatory documentation
* Healthcare knowledge bases
* Private enterprise knowledge management

---

## Features

* PDF and text document ingestion
* Semantic search using vector embeddings
* ChromaDB vector database
* Persistent storage with Kubernetes StatefulSets
* Local LLM inference using Ollama
* LangGraph agent integration
* RAGAS evaluation framework
* Kubernetes-native deployment
* Zero external LLM API dependency

---

## Evaluation

RAGAS Results

| Metric            | Score |
| ----------------- | ----- |
| Faithfulness      | 1.00  |
| Context Precision | 1.00  |
| Context Recall    | 1.00  |
| Answer Relevancy  | 0.82  |

Evaluation performed locally against the sample HR policy corpus.

---

## Architecture

User
↓
FastAPI API
↓
RAG Pipeline
├── ChromaDB
│ └── Persistent Volume Claim
└── Ollama
└── Llama 3

Kubernetes Components

| Component      | Kubernetes Resource |
| -------------- | ------------------- |
| FastAPI API    | Deployment          |
| Ollama         | Deployment          |
| ChromaDB       | StatefulSet         |
| Chroma Storage | PVC                 |
| Model Puller   | Job                 |

---

## API Endpoints

Health Check

```bash
curl http://localhost:8000/
```

Upload Document

```bash
curl -F "file=@document.pdf" \
http://localhost:8000/upload
```

Ingest Existing File

```bash
curl -X POST http://localhost:8000/ingest \
-H "Content-Type: application/json" \
-d '{"file_path":"app/data/policy.txt"}'
```

Ask Question

```bash
curl -X POST http://localhost:8000/ask \
-H "Content-Type: application/json" \
-d '{"question":"How many vacation days do employees get?"}'
```

Agent Endpoint

```bash
curl -X POST http://localhost:8000/agent \
-H "Content-Type: application/json" \
-d '{"question":"Summarize the vacation policy"}'
```

---

## Technology Stack

| Technology       | Purpose          |
| ---------------- | ---------------- |
| FastAPI          | API Layer        |
| ChromaDB         | Vector Database  |
| Ollama           | Model Serving    |
| Llama 3          | LLM              |
| nomic-embed-text | Embeddings       |
| LangGraph        | Agent Framework  |
| Docker           | Containerization |
| Kind             | Local Kubernetes |
| Kubernetes       | Orchestration    |
| RAGAS            | Evaluation       |

---

## Deployment

Build Image

```bash
docker build -t rag-app:latest .
```

Load Into Kind

```bash
kind load docker-image rag-app:latest --name rag-dev
```

Deploy Components

```bash
kubectl apply -f chroma-statefulset.yaml
kubectl apply -f ollama-deployment.yaml
kubectl apply -f ollama-model-pull-job.yaml
kubectl apply -f rag-app-deployment.yaml
```

Access API

```bash
kubectl port-forward deploy/rag-app 8000:8000
```

---

## What I Learned

* Building end-to-end RAG systems
* Semantic search with embeddings
* Vector database design
* Kubernetes Deployments vs StatefulSets
* Persistent storage using PVCs
* Local LLM hosting with Ollama
* LangGraph agent workflows
* RAG evaluation with RAGAS
* Service-to-service communication inside Kubernetes

---

## Roadmap

* [x] PDF ingestion
* [x] Semantic retrieval
* [x] ChromaDB StatefulSet
* [x] Persistent storage
* [x] Ollama deployment
* [x] LangGraph agent
* [x] RAGAS evaluation
* [ ] EKS deployment
* [ ] S3 document storage
* [ ] Hybrid search
* [ ] Reranking
* [ ] LangSmith observability
* [ ] Multi-document collections
* [ ] Authentication and RBAC
* [ ] Whisper voice ingestion
