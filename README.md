# Private RAG Platform

A privacy-first Retrieval-Augmented Generation (RAG) platform for querying private documents without sending data to external AI providers.

Built with FastAPI, ChromaDB, Ollama, Llama 3, LangGraph, and Kubernetes.

---

## Overview

This project enables organizations to upload internal documents and ask natural language questions against them using a locally hosted LLM. All inference, embeddings, retrieval, and storage remain inside the cluster. Zero data leaves your infrastructure.

```
PDF/TXT Upload
      ↓
FastAPI
      ↓
Chunking (300 char chunks)
      ↓
Embeddings (nomic-embed-text)
      ↓
ChromaDB (vector storage)
      ↓
Semantic Search
      ↓
Llama 3 (Ollama)
      ↓
Answer
```

Potential use cases:
- Legal document search
- Internal policy assistants
- Compliance and regulatory documentation
- Healthcare knowledge bases
- Private enterprise knowledge management

---

## Features

- PDF and text document ingestion
- File upload endpoint for dynamic document ingestion
- Semantic search using vector embeddings
- ChromaDB as a StatefulSet with persistent storage
- Local LLM inference using Ollama
- Declarative model pulling via init container
- LangGraph agent integration
- RAGAS evaluation framework
- Kubernetes-native deployment (Kind locally, EKS in production)
- Docker Compose support for local development
- Zero external LLM API dependency

---

## RAGAS Evaluation Results

Evaluation performed locally against the sample HR policy corpus.

| Metric            | Score |
|-------------------|-------|
| Faithfulness      | 1.00  |
| Context Precision | 1.00  |
| Context Recall    | 1.00  |
| Answer Relevancy  | 0.82  |

Faithfulness of 1.00 means every answer came from the document, not hallucination.
Answer Relevancy of 0.82 indicates room to improve chunking strategy for more precise retrieval.

---

## Architecture

```
User
  ↓
POST /upload or /ask or /agent
  ↓
FastAPI Deployment (rag-app)
  ↓              ↓
Chroma        Ollama
StatefulSet   Deployment
  ↓
PVC Storage (persistent)
```

### Kubernetes Components

| Component      | Resource    | Why                                              |
|----------------|-------------|--------------------------------------------------|
| FastAPI API    | Deployment  | Stateless, pods are interchangeable              |
| Ollama         | Deployment  | Stateless server, models stored on PVC           |
| ChromaDB       | StatefulSet | Stateful, needs stable identity and storage      |
| Chroma Storage | PVC 5GB     | Survives pod restarts                            |
| Ollama Storage | PVC 10GB    | Models persist across pod restarts               |
| Model Puller   | Init Container | Declarative model pulling on pod start        |

### Why StatefulSet for Chroma

Kubernetes Deployments are for stateless apps. Pods are interchangeable and start fresh on restart. ChromaDB stores vector data on disk — that is stateful. StatefulSet gives the pod stable identity and keeps its storage attached across restarts. Without this, every Chroma pod restart would wipe all ingested documents.

### Why Recreate Strategy for Ollama

Ollama uses a PVC with `ReadWriteOnce` access mode, meaning only one pod can mount it at a time. The default `RollingUpdate` strategy tries to start a new pod before killing the old one. Both pods fight over the same PVC and the new pod gets stuck Pending. `Recreate` strategy kills the old pod first, then starts the new one. Solves the conflict.

```yaml
strategy:
  type: Recreate
```

---

## Key Engineering Decisions and Findings

### Declarative Model Pulling

Bad approach — Kubernetes Job:
```
Job runs once
Models download into ephemeral pod storage
Pod restarts
Models gone
Manual re-pull required
```

Good approach — Init Container in Ollama Deployment:
```yaml
initContainers:
- name: pull-models
  image: ollama/ollama:latest
  command: ["/bin/sh", "-c"]
  args:
    - |
      ollama serve &
      sleep 5
      ollama pull llama3
      ollama pull nomic-embed-text
      kill %1
  volumeMounts:
  - name: ollama-data
    mountPath: /root/.ollama
```

Every pod restart runs the init container first. If models are already in the PVC, Ollama skips the download. If they are missing, it pulls them. No manual steps. Git is the source of truth.

### The PORT Environment Variable Conflict

When you name a Kubernetes Service `chroma` running on port 8000, Kubernetes automatically injects this into every pod in the namespace:

```
PORT=tcp://10.96.149.181:8000
```

ChromaDB's Rust backend expects PORT to be a plain number like `8000`. It receives the full TCP string and panics:

```
Error loading config: invalid type: found string "tcp://10.96.149.181:8000",
expected u16 for key "PORT"
```

Fix — explicitly override PORT in the StatefulSet env block:

```yaml
env:
- name: IS_PERSISTENT
  value: "TRUE"
- name: PERSIST_DIRECTORY
  value: "/data"
- name: CHROMA_PORT
  value: "8000"
```

Lesson: Kubernetes injects environment variables automatically for service discovery. Variable names like `PORT`, `HOST`, and `USER` can conflict with what your application expects.

### python-multipart and Why It Breaks in Kubernetes

FastAPI requires `python-multipart` to handle file uploads. On your Mac, it may already be installed as a side effect of other packages. Inside a Docker container, only what is explicitly in `requirements.txt` gets installed.

If missing, the upload endpoint crashes with:
```
RuntimeError: Form data requires "python-multipart"
```

Fix: add it to `requirements.txt` before building the image.

Lesson: your local machine is not a clean environment. Docker containers are. Always test with a fresh container before deploying.

### LangGraph Tool Calling Model Requirement

LangGraph agents require a model that supports tool calling. `llama3` (original) does not support tools and returns:

```
llama3 does not support tools (status code: 400)
```

Models that support tool calling: `llama3.1`, `llama3.2`, `llama3.3`.

### Memory Requirements for LangGraph Agent

LangGraph agents make multiple sequential LLM calls per request (reasoning + tool call + synthesis). On an Intel Mac with limited Docker Desktop memory allocation, this causes OOMKill:

```
kind cluster + Ollama + llama3.1 + Chroma + FastAPI + LangGraph
= exceeds available memory on 7GB Docker allocation
```

Fix for local development: increase Docker Desktop memory to 16-20GB (safe on a 32GB Mac).

Production fix: EKS with a GPU node (`g4dn.xlarge`). Agent runs in under 10 seconds with GPU acceleration.

### Kubernetes DNS — Inside vs Outside the Cluster

Service names resolve inside the cluster only:

```
chroma  → resolves to Chroma pod IP inside the cluster
ollama  → resolves to Ollama pod IP inside the cluster
```

From your Mac, these hostnames do not resolve. Always port-forward to access services locally:

```bash
kubectl port-forward svc/chroma 8001:8000
kubectl port-forward deploy/rag-app 8000:8000
```

### Environment Variables vs Hardcoded Paths

Bad:
```python
client = chromadb.PersistentClient(path="./chroma_db")
```

Good:
```python
import os
CHROMA_PATH = os.getenv("CHROMA_PATH", "/data/chroma_db")
client = chromadb.PersistentClient(path=CHROMA_PATH)
```

Same principle as Terraform variables and Kubernetes ConfigMaps. Configuration separated from code. Works in every environment without changing the application.

### Chunking Strategy

Documents are split into 300-character chunks. Chunk size directly affects answer quality:

- Too large → Chroma retrieves irrelevant sections, confuses the model
- Too small → important context gets cut off mid-sentence
- 300 characters → good baseline for short policy documents
- 500-800 characters → better for legal documents with long clauses

RAGAS Answer Relevancy score of 0.82 suggests chunking can be improved. Next iteration will test semantic chunking instead of fixed character splits.

---

## API Reference

### GET /
Health check.
```bash
curl http://localhost:8000/
# {"status": "running"}
```

### POST /upload
Upload any PDF. Ingests into Chroma automatically.
```bash
curl -F "file=@contract.pdf" http://localhost:8000/upload
# {"filename": "contract.pdf", "chunks_added": 39, "status": "ingested"}
```

### POST /ingest
Ingest a file already inside the container.
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"file_path": "app/data/policy.txt"}'
```

### POST /ask
Fixed RAG pipeline. Always searches documents.
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How many vacation days do employees get?"}'
# {
#   "question": "...",
#   "route": "local_ollama_private_rag",
#   "answer": "...",
#   "retrieved_chunks": ["..."]
# }
```

### POST /agent
LangGraph agent. LLM decides whether to call the document search tool.
```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the vacation policy"}'
# {
#   "question": "...",
#   "route": "langgraph_agent",
#   "answer": "..."
# }
```

The difference between /ask and /agent:
```
/ask   → deterministic, always searches documents
/agent → LLM decides what tool to use, can reason across multiple steps
```

---

## File Structure

```
local-rag-api/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI routes
│   ├── rag.py               ← RAG pipeline logic
│   ├── agent.py             ← LangGraph agent
│   ├── ollama_client.py     ← Ollama API calls
│   └── data/
│       └── policy.txt       ← Sample HR policy document
├── eval/
│   └── ragas_eval.py        ← RAGAS evaluation script
├── Dockerfile
├── docker-compose.yml       ← Local development
├── requirements.txt
├── chroma-statefulset.yaml
├── ollama-deployment.yaml
└── rag-app-deployment.yaml
```

---

## Local Development with Docker Compose

Compose removes the Kubernetes overhead layer for faster local iteration:

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3
docker compose exec ollama ollama pull llama3.1
docker compose exec ollama ollama pull nomic-embed-text
```

Test:
```bash
curl -F "file=@document.pdf" http://localhost:8000/upload
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the main points?"}'
```

---

## Kubernetes Deployment (Kind — Local)

```bash
# Create cluster
kind create cluster --name rag-dev

# Build and load image
docker build -t rag-app:latest .
kind load docker-image rag-app:latest --name rag-dev

# Deploy
kubectl apply -f chroma-statefulset.yaml
kubectl apply -f ollama-deployment.yaml
kubectl apply -f rag-app-deployment.yaml

# Verify
kubectl get pods
# chroma-0     Running
# ollama-xxx   Running
# rag-app-xxx  Running

# Access
kubectl port-forward deploy/rag-app 8000:8000
```

---

## Technology Stack

| Technology        | Purpose                  |
|-------------------|--------------------------|
| FastAPI           | API layer                |
| ChromaDB          | Vector database          |
| Ollama            | Local LLM server         |
| Llama 3           | Language model           |
| Llama 3.1/3.2     | Tool-calling agent model |
| nomic-embed-text  | Embedding model          |
| LangGraph         | Agent framework          |
| pypdf             | PDF text extraction      |
| python-multipart  | File upload handling     |
| Docker            | Containerization         |
| Kind              | Local Kubernetes         |
| Kubernetes        | Orchestration            |
| RAGAS             | RAG evaluation           |

---

## What I Built and Learned

### Built
- End-to-end private RAG system from scratch
- File upload endpoint for dynamic document ingestion
- ChromaDB StatefulSet with persistent volume
- Ollama with declarative model pulling via init container
- LangGraph agent wrapping RAG as a tool
- RAGAS evaluation pipeline
- Full Kubernetes multi-pod architecture

### Learned
- Kubernetes Deployments vs StatefulSets and when to use each
- Recreate vs RollingUpdate strategy for stateful workloads
- Persistent storage using PVCs and how data survives pod restarts
- Declarative vs imperative operations in Kubernetes
- Kubernetes DNS and service-to-service communication
- Why environment variables conflict with Kubernetes service injection
- async/await in FastAPI and why it matters for concurrent uploads
- Configuration separated from code using environment variables
- LangGraph tool calling and model requirements
- Memory constraints for LLM inference in containerized environments
- RAGAS metrics and what each score means for RAG quality
- ⏳ Docker Compose — setting up now
- ⬜ Test LangGraph agent (blocked by memory on Kind)
---

## Roadmap

- [x] PDF ingestion
- [x] Semantic retrieval
- [x] ChromaDB StatefulSet with PVC
- [x] Persistent storage
- [x] Ollama deployment
- [x] Declarative model pulling via init container
- [x] File upload endpoint
- [x] LangGraph agent
- [x] RAGAS evaluation
- [x] Docker Compose local dev
- [ ] EKS deployment with Terraform
- [ ] GPU node for agent inference (g4dn.xlarge)
- [ ] S3 document storage for raw files
- [ ] Hybrid search (vector + keyword)
- [ ] Reranking for better retrieval
- [ ] LangSmith observability
- [ ] Multi-document collections
- [ ] Authentication and RBAC
- [ ] Whisper voice ingestion
- [ ] Frontend UI for non-technical users/