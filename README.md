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
ChromaDB (vector storage, metadata-aware)
      ↓
Semantic Search (cosine similarity, optional document filter)
      ↓
Llama 3 / 3.1 (Ollama)
      ↓
Answer + Sources + Grounding Verification
```

Potential use cases:
- Legal document search
- Internal policy assistants
- Compliance and regulatory documentation
- Healthcare knowledge bases
- Private enterprise knowledge management
- Multi-document research across a private corpus

---

## Features

- PDF and text document ingestion
- File upload endpoint for dynamic document ingestion
- Semantic search using vector embeddings (cosine similarity)
- ChromaDB as a StatefulSet with persistent storage
- Local LLM inference using Ollama
- Declarative model pulling via init container
- LangGraph agent integration with tool-calling
- **Source citations on every answer** (`/ask` and `/agent`)
- **Agent grounding verification** (`tool_was_called`) — proves the agent queried documents instead of answering from training data
- **Metadata-aware retrieval** — chunks tagged with `document_name` and `chunk_index`, queries filterable to a single document
- **Document registry endpoint** (`/documents`) — lists every document currently indexed
- **Retrieval debugging endpoint** (`/debug-search`) — inspect raw retrieval output before generation, to isolate retrieval failures from generation failures
- RAGAS evaluation framework
- Kubernetes-native deployment (Kind locally, EKS in production)
- Docker Compose support for local development
- Zero external LLM API dependency

---

## RAGAS Evaluation Results

Evaluation performed locally against the sample HR policy corpus, using the system's own local Llama 3 as the judge model (no OpenAI dependency).

| Metric            | Score |
|--------------------|------|
| Faithfulness       | 1.00 |
| Context Precision  | 1.00 |
| Context Recall     | 1.00 |
| Answer Relevancy   | 0.82 |

Faithfulness of 1.00 means every answer was supported by the retrieved document content, not hallucinated. Answer Relevancy of 0.82 indicates room to improve chunking strategy for more precise retrieval.

This evaluation currently covers the `/ask` endpoint only. Extending it to cover `/agent` and the trading-strategy document is on the roadmap (see below) — early manual testing surfaced an inconsistent-faithfulness issue in agent generation, addressed under Key Engineering Decisions.

---

## Architecture

```
User
  ↓
POST /upload, /ask, /agent, /debug-search, GET /documents
  ↓
FastAPI Deployment (rag-app)
  ↓              ↓
Chroma        Ollama
StatefulSet   Deployment
  ↓
PVC Storage (persistent)
```

### Kubernetes Components

| Component       | Resource        | Why                                          |
|-------------------|------------------|-----------------------------------------------|
| FastAPI API      | Deployment       | Stateless, pods are interchangeable            |
| Ollama           | Deployment       | Stateless server, models stored on PVC         |
| ChromaDB         | StatefulSet      | Stateful, needs stable identity and storage    |
| Chroma Storage   | PVC 5GB          | Survives pod restarts                          |
| Ollama Storage   | PVC 10GB         | Models persist across pod restarts             |
| Model Puller     | Init Container   | Declarative model pulling on pod start         |

### Why StatefulSet for Chroma

Kubernetes Deployments are for stateless apps. Pods are interchangeable and start fresh on restart. ChromaDB stores vector data on disk — that is stateful. StatefulSet gives the pod stable identity and keeps its storage attached across restarts. Without this, every Chroma pod restart would wipe all ingested documents.

### Why Recreate Strategy for Ollama

Ollama uses a PVC with `ReadWriteOnce` access mode, meaning only one pod can mount it at a time. The default `RollingUpdate` strategy tries to start a new pod before killing the old one. Both pods fight over the same PVC and the new pod gets stuck Pending. `Recreate` strategy kills the old pod first, then starts the new one.

```yaml
strategy:
  type: Recreate
```

---

## Key Engineering Decisions and Findings

### Declarative Model Pulling

Bad approach — Kubernetes Job: runs once, models download into ephemeral pod storage, pod restarts, models gone, manual re-pull required.

Good approach — Init Container in the Ollama Deployment:

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
      ollama pull llama3.1
      ollama pull nomic-embed-text
      kill %1
  volumeMounts:
  - name: ollama-data
    mountPath: /root/.ollama
```

Every pod restart runs the init container first. If models are already in the PVC, Ollama skips the download. If they are missing, it pulls them. No manual steps. Git is the source of truth.

### The PORT Environment Variable Conflict

Naming a Kubernetes Service `chroma` on port 8000 causes Kubernetes to auto-inject `PORT=tcp://10.96.149.181:8000` into every pod in the namespace. ChromaDB's backend expects `PORT` as a plain integer and panics on the injected string.

Fix — explicitly override in the StatefulSet env block:

```yaml
env:
- name: IS_PERSISTENT
  value: "TRUE"
- name: PERSIST_DIRECTORY
  value: "/data"
- name: CHROMA_PORT
  value: "8000"
```

Lesson: Kubernetes injects environment variables automatically for service discovery. Generic variable names (`PORT`, `HOST`, `USER`) can silently collide with what an application expects.

### python-multipart and Why It Breaks in Kubernetes

FastAPI requires `python-multipart` to handle file uploads. It may already exist on a local machine as a side effect of other installed packages, but inside a container, only what's explicitly listed in `requirements.txt` gets installed. Missing it crashes the upload endpoint with `RuntimeError: Form data requires "python-multipart"`.

Lesson: a local machine is not a clean environment. Always test with a fresh container before deploying.

### LangGraph Tool Calling Model Requirement

LangGraph agents require a model that supports tool calling. `llama3` (original) does not and returns `llama3 does not support tools (status code: 400)`. Models that do: `llama3.1`, `llama3.2`, `llama3.3`.

### Memory Requirements for the LangGraph Agent

LangGraph agents make multiple sequential LLM calls per request (reasoning, tool call, synthesis). On an Intel Mac with limited Docker Desktop memory, this caused OOMKill inside Kind:

```
kind cluster + Ollama + llama3.1 + Chroma + FastAPI + LangGraph
= exceeds available memory on a 7GB Docker allocation
```

Local fix: moved local agent testing to Docker Compose, removing the Kubernetes-in-Docker overhead layer entirely. Production fix: EKS with a GPU node (`g4dn.xlarge`).

### Kubernetes DNS — Inside vs Outside the Cluster

Service names (`chroma`, `ollama`) resolve inside the cluster only. From a local machine, they don't resolve — always port-forward to reach services locally:

```bash
kubectl port-forward svc/chroma 8001:8000
kubectl port-forward deploy/rag-app 8000:8000
```

### Environment Variables vs Hardcoded Paths

```python
# Bad
client = chromadb.PersistentClient(path="./chroma_db")

# Good
import os
CHROMA_PATH = os.getenv("CHROMA_PATH", "/data/chroma_db")
client = chromadb.PersistentClient(path=CHROMA_PATH)
```

Same principle as Terraform variables and Kubernetes ConfigMaps — configuration separated from code, so the same image runs unmodified in every environment.

### Chunking Strategy

Documents are split into 300-character chunks. Too large and Chroma retrieves irrelevant sections that confuse the model; too small and important context gets cut off mid-sentence. 300 characters is a reasonable baseline for short policy documents; legal documents with long clauses likely need 500–800. The 0.82 Answer Relevancy score suggests this is worth revisiting — semantic chunking instead of fixed character splits is on the roadmap.

### Generation Inconsistency: Temperature Tuning

Manual testing surfaced a faithfulness problem distinct from retrieval. Across repeated identical queries against `/agent`, the same retrieved context sometimes produced a correct, grounded answer and sometimes produced fabricated detail not present in the source (e.g., inventing unrelated trading-indicator explanations for terms that were never defined that way in the document). Retrieval was verified correct and consistent in every run via `/debug-search`; only generation varied.

Root cause: `ChatOllama`'s default non-zero sampling temperature, which is appropriate for creative generation but undesirable for a RAG system whose job is to relay retrieved content faithfully, not embellish it.

Fix:

```python
llm = ChatOllama(
    model="llama3.1",
    base_url="http://ollama:11434",
    temperature=0
)
```

Confirmed consistent, grounded answers across repeated runs after the change.

### Metadata Schema Migration

After adding `document_name` and `chunk_index` to chunk metadata, previously ingested chunks still carried the old schema (`{"source": file_path}` only) and did not retroactively update — changing the ingestion code does not change records already written to the database. `/debug-search` surfaced this directly, returning `document_name: null` on old records.

Since this was disposable development data, the fix was to reset the local volume and re-ingest:

```bash
docker compose down -v
docker compose up -d --build
```

In production, this is the wrong move — wiping a live vector store destroys real data with no rollback. The correct pattern is a versioned collection:

```python
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "private_docs")
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)
```

Migration flow: keep the old collection live, create `private_docs_v2` with the corrected schema, re-index all source documents from S3 into it, validate retrieval via `/debug-search`, switch the application's `COLLECTION_NAME` env var, monitor, then delete the old collection later. Rollback at every step.

### Distance Metric: Cosine vs L2

Chroma defaults to squared L2 distance, not cosine similarity. Computing `relevance_score = 1 - distance` against raw L2 distances on un-normalized embeddings produces meaningless values (observed as large negative numbers). Fix: explicitly set the collection's distance metric at creation time —

```python
collection = client.get_or_create_collection(
    name="private_docs",
    metadata={"hnsw:space": "cosine"}
)
```

This only applies going forward; an existing collection's metric can't be changed in place, which is part of why the metadata migration above required a fresh collection rather than an in-place fix.

---

## API Reference

### `GET /`
Health check.
```bash
curl http://localhost:8000/
# {"status": "running"}
```

### `POST /upload`
Upload a PDF. Ingests into Chroma automatically, tagged with `document_name`.
```bash
curl -F "file=@contract.pdf" http://localhost:8000/upload
# {"filename": "contract.pdf", "chunks_added": 39, "status": "ingested"}
```

### `POST /ingest`
Ingest a file already inside the container.
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"file_path": "app/data/policy.txt"}'
```

### `GET /documents`
List every document currently indexed in the vector store. Powers document-selector UI.
```bash
curl http://localhost:8000/documents
# {"documents": ["policy.txt", "Reggie_MGC_Trading_Strategy.pdf"]}
```

### `POST /ask`
Deterministic RAG pipeline. Always searches documents. Accepts an optional `document_name` to constrain retrieval to a single file.
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "How many vacation days do employees get?", "document_name": "policy.txt"}'
# {
#   "question": "...",
#   "route": "local_ollama_private_rag",
#   "answer": "...",
#   "sources": [{"document": "policy.txt", "excerpt": "...", "relevance_score": 0.91}],
#   "retrieved_chunks": ["..."]
# }
```

### `POST /agent`
LangGraph agent. The LLM decides whether to call the document search tool, and can reason across multiple steps. Returns grounding verification.
```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the four gates for entry?"}'
# {
#   "question": "...",
#   "route": "langgraph_agent",
#   "answer": "...",
#   "sources": [...],
#   "tool_was_called": true
# }
```

`tool_was_called: false` with a confident-sounding answer is the signature of the silent-failure case: the agent skipped retrieval and answered from training data instead of the actual document. This field exists specifically to catch that.

### `POST /debug-search`
Raw retrieval output, no generation step. Used to isolate retrieval failures from generation failures before debugging a hallucination.
```bash
curl -X POST http://localhost:8000/debug-search \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the four gates for entry?"}'
# {
#   "query": "...",
#   "top_chunks": ["..."],
#   "scores": [0.6654, 0.521, 0.492],
#   "metadatas": [{"document_name": "Reggie_MGC_Trading_Strategy.pdf", "chunk_index": 12}, ...]
# }
```

### Endpoint comparison

```
/ask          → deterministic, always searches documents
/agent        → LLM decides whether/what tool to call, can reason across steps
/debug-search → retrieval only, no LLM generation — for diagnosing where a failure lives
```

---

## File Structure

```
local-rag-api/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI routes
│   ├── rag.py               ← RAG pipeline + metadata-aware retrieval + debug search
│   ├── agent.py             ← LangGraph agent, temperature=0
│   ├── ollama_client.py     ← Ollama API calls
│   └── data/
│       └── policy.txt       ← Sample HR policy document
├── eval/
│   ├── ragas_eval.py        ← RAGAS evaluation script (covers /ask)
│   └── evaluate_retrieval.py ← Retrieval-accuracy pass/fail harness (covers document routing)
├── Dockerfile
├── docker-compose.yml       ← Local development
├── requirements.txt
├── chroma-statefulset.yaml
├── ollama-deployment.yaml
└── rag-app-deployment.yaml
```

---

## Local Development with Docker Compose

Compose removes the Kubernetes-in-Docker overhead layer for faster local iteration, and was specifically what unblocked LangGraph agent testing after Kind ran out of memory.

```bash
docker compose up -d
docker compose exec ollama ollama pull llama3
docker compose exec ollama ollama pull llama3.1
docker compose exec ollama ollama pull nomic-embed-text
```

```bash
curl -F "file=@document.pdf" http://localhost:8000/upload
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the main points?"}'
```

---

## Kubernetes Deployment (Kind — Local)

```bash
kind create cluster --name rag-dev

docker build -t rag-app:latest .
kind load docker-image rag-app:latest --name rag-dev

kubectl apply -f chroma-statefulset.yaml
kubectl apply -f ollama-deployment.yaml
kubectl apply -f rag-app-deployment.yaml

kubectl get pods
# chroma-0     Running
# ollama-xxx   Running
# rag-app-xxx  Running

kubectl port-forward deploy/rag-app 8000:8000
```

---

## Technology Stack

| Technology         | Purpose                    |
|----------------------|------------------------------|
| FastAPI             | API layer                   |
| ChromaDB             | Vector database              |
| Ollama               | Local LLM server             |
| Llama 3              | Language model                |
| Llama 3.1/3.2        | Tool-calling agent model      |
| nomic-embed-text     | Embedding model               |
| LangGraph            | Agent framework               |
| pypdf                | PDF text extraction           |
| python-multipart     | File upload handling          |
| Docker               | Containerization               |
| Kind                 | Local Kubernetes               |
| Kubernetes           | Orchestration                  |
| RAGAS                | RAG evaluation                 |

---

## What I Built and Learned

### Built
- End-to-end private RAG system from scratch
- File upload endpoint for dynamic document ingestion
- ChromaDB StatefulSet with persistent volume, explicit cosine distance metric
- Ollama with declarative model pulling via init container
- LangGraph agent wrapping RAG as a tool, with temperature tuned for faithful generation
- Source citation system on every answer, for retrieval transparency
- Agent grounding verification (`tool_was_called`) to detect silent tool-skip failures
- Metadata-aware retrieval with per-document filtering
- Document registry endpoint for frontend document selection
- Retrieval debugging endpoint to separate retrieval failures from generation failures
- RAGAS evaluation pipeline
- Retrieval-accuracy evaluation harness with pass/fail output
- Full Kubernetes multi-pod architecture

### Learned
- Kubernetes Deployments vs StatefulSets and when to use each
- Recreate vs RollingUpdate strategy for stateful workloads
- Persistent storage using PVCs and how data survives pod restarts
- Declarative vs imperative operations in Kubernetes
- Kubernetes DNS and service-to-service communication
- Why environment variables conflict with Kubernetes service injection
- Configuration separated from code using environment variables
- LangGraph tool calling and model requirements
- Memory constraints for LLM inference in containerized environments
- RAGAS metrics and what each score means for RAG quality
- Cosine vs L2 distance and why the metric must be set explicitly at collection creation
- Why changing ingestion code does not retroactively change already-stored vector records, and why production handles this with a versioned collection and re-index job rather than a destructive reset
- Sampling temperature's effect on RAG faithfulness, and why zero temperature is the right default for context-grounded generation
- Why container filesystem paths (`/tmp`) must be used instead of local machine paths, since a container can't see the host filesystem

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
- [x] Source citations
- [x] Agent tool telemetry (`tool_was_called`)
- [x] Metadata-aware multi-document retrieval
- [x] Document registry endpoint (`/documents`)
- [x] Retrieval debugging endpoint (`/debug-search`)
- [x] Temperature tuning for generation consistency
- [ ] Retrieval-accuracy evaluation harness across multiple documents (in progress)
- [ ] Extend RAGAS evaluation to cover `/agent` and the trading-strategy document
- [ ] Frontend UI: upload, document selector, question box, sources panel, agent toggle
- [ ] EKS deployment with Terraform
- [ ] GPU node for agent inference (`g4dn.xlarge`)
- [ ] S3 document storage for raw files, with versioned-collection migration pattern
- [ ] Hybrid search (vector + keyword)
- [ ] Reranking for better retrieval
- [ ] LangSmith observability
- [ ] Authentication and RBAC
- [ ] Whisper voice ingestion



Things to fit in to reame clealy throu openai or antrhopic 
LangSmith or Phoenix tracing
RAGAS evaluations
Hybrid retrieval
Reranking
Authentication
Usage tracking
Async ingestion
Qdrant
vLLM serving
EKS deployment




----------

This is much closer to reality than the first "ASI Engineer" post.

I'd say **85–90% accurate** for what senior AI engineers are doing in 2026.

### What's accurate

#### Senior AI engineers don't just write prompts

This is probably the biggest misconception.

Junior people think:

> AI Engineer = Prompt Engineer

Senior people know:

> AI Engineer = Distributed systems engineer with LLMs in the middle.

The hard problems are:

* reliability
* evaluation
* observability
* cost
* latency
* security
* retrieval quality

not prompt wording.

---

#### RAG optimization is a real senior-level skill

Almost everyone can build:

FastAPI → Chroma → OpenAI/Ollama

in a weekend.

Very few can explain:

* hybrid retrieval
* reranking
* parent-child retrieval
* contextual chunking
* metadata filtering
* query expansion
* evaluation metrics

That's where the value is.

Your current project is actually sitting right at this transition point.

You already have:

* FastAPI
* Chroma
* Ollama
* Kubernetes

The next level is:

```text
Query
 ↓
Hybrid Search
 ↓
Top 50 Docs
 ↓
Reranker
 ↓
Top 5 Docs
 ↓
LLM
 ↓
Evaluation
 ↓
Trace Storage
```

---

#### Evaluation is becoming mandatory

A year ago people were shipping:

```python
response = llm.invoke(prompt)
return response
```

Today interviewers increasingly ask:

> How do you know your AI system is good?

Expected answers:

* RAGAS
* Braintrust
* LangSmith
* Phoenix
* custom evaluation datasets

This is one of the biggest differences between junior and senior AI engineers.

---

#### Rate limiting and async architecture

Very real.

A lot of tutorial projects look like:

```python
user
 ↓
fastapi
 ↓
openai
```

Production systems look more like:

```text
User
 ↓
API Gateway
 ↓
FastAPI
 ↓
Redis Queue
 ↓
Workers
 ↓
LLM
 ↓
Result Store
```

Or:

```text
FastAPI
 ↓
Celery
 ↓
Redis
 ↓
Workers
```

because thousands of concurrent requests will crush a naïve design.

---

### What I'd change

#### "Use few-shot examples to fix hallucinations"

That's only partially true.

Hallucinations are usually addressed through:

1. Better retrieval
2. Better grounding
3. Better context
4. Better model selection
5. Better evaluation

Few-shot examples help, but they are not the primary fix.

If your retrieval is garbage, few-shot prompting won't save you.

---

#### "Cross-encoder reranker" isn't always necessary

Good answer in an interview.

Bad answer as a universal solution.

A senior engineer would say:

> We measured retrieval quality and added reranking because it improved relevance.

Not:

> Always add a reranker.

Everything should be benchmarked.

---

#### "Senior AI Engineer builds multi-agent systems"

Sometimes.

The internet currently overhypes agents.

Many production systems are still:

```text
User
 ↓
Retriever
 ↓
LLM
 ↓
Answer
```

A lot of companies have learned that:

* simple systems
* fewer moving parts
* easier debugging

often outperform complicated agent swarms.

---

### For YOU specifically

Looking at what you've already built:

Current level:

```text
FastAPI
 ↓
Chroma
 ↓
Ollama
```

Immediate next skills:

1. LangSmith or Phoenix tracing
2. RAGAS evaluations
3. Hybrid retrieval
4. Reranking
5. Authentication
6. Usage tracking
7. Async ingestion
8. Qdrant
9. vLLM serving
10. EKS deployment

```

If you complete those, you can legitimately discuss:

- retrieval failures
- vector search tuning
- evaluation frameworks
- observability
- production inference

in interviews.

That's much closer to a real AI Engineer than spending six months studying transformer math.

### The one thing missing

The post barely mentions what separates **senior** from **mid-level**:

**Tradeoffs.**

Senior answers sound like:

> "We tested semantic chunking versus fixed chunks and fixed chunks were cheaper with similar recall, so we kept fixed chunks."

or

> "We removed an agent because latency increased 4× and users didn't see better outcomes."

or

> "We chose Qdrant over Chroma because we needed filtering and clustering at scale."

Anybody can name tools.

Senior engineers explain **why one design was chosen over another and what metrics justified the decision**. That's what interviewers usually probe for.
```
