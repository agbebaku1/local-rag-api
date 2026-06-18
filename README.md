# local-rag-api

A private RAG (Retrieval-Augmented Generation) system that runs completely on your own infrastructure. Upload a PDF, ask questions, get answers — zero data leaves your machine or cluster.

Built with FastAPI, ChromaDB, Ollama, Llama 3, LangGraph, and Kubernetes.

---

## What This Does

Most AI tools send your data to OpenAI or Anthropic. This system does not. Everything — inference, embeddings, retrieval, storage — runs locally.

```
PDF/TXT Upload
      ↓
FastAPI
      ↓
Idempotency check (content_hash) — skip if already ingested
      ↓
Chunking (300 char chunks)
      ↓
Embeddings (nomic-embed-text)
      ↓
ChromaDB (vector storage, cosine similarity, metadata-aware)
      ↓
Semantic Search (optional per-document filter)
      ↓
Llama 3 / 3.1 (Ollama)
      ↓
Answer + Sources + Grounding Verification
```

Real use cases this is aimed at:
- A law firm uploading case files and querying across all of them
- A healthcare client querying compliance policy without sending data to the cloud
- Any organization with sensitive documents it can't send to a third-party API

---

## Features

- PDF and text document ingestion
- File upload endpoint for dynamic ingestion
- **Idempotent ingestion** — uploading the same document content twice does not duplicate vectors, detected via SHA-256 content hash, independent of filename
- Semantic search using vector embeddings, explicit cosine similarity metric
- ChromaDB as a StatefulSet with persistent storage
- Local LLM inference via Ollama
- Declarative model pulling via init container
- LangGraph agent with tool-calling, temperature tuned for faithful generation
- Source citations on every answer (`/ask` and `/agent`)
- Agent grounding verification (`tool_was_called`) — proves the agent actually queried documents instead of answering from training data
- Metadata-aware retrieval — chunks tagged with `document_name`, `chunk_index`, and `content_hash`; queries filterable to a single document. **Tested with two real, topically distinct documents** — see Key Engineering Decisions below.
- Document registry endpoint (`/documents`)
- Retrieval debugging endpoint (`/debug-search`) — inspect raw retrieval before generation, to separate retrieval failures from generation failures
- RAGAS evaluation framework
- A retrieval-accuracy pass/fail harness (`evaluate_retrieval.py`) — 6 test cases across both real documents, 100% accuracy, no filter applied
- Kubernetes-native deployment (Kind locally; EKS planned)
- Docker Compose for local development
- Zero external LLM API dependency

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

Three pods talking to each other by Kubernetes service name:

| Pod | Role | Type |
|-----|------|------|
| rag-app | FastAPI API server | Deployment |
| chroma-0 | Vector database | StatefulSet |
| ollama | LLM server (Llama 3 / 3.1) | Deployment |

### Why StatefulSet for Chroma

Kubernetes Deployments are for stateless apps — pods are interchangeable, and a restarted pod starts fresh. ChromaDB stores vector data on disk, which is stateful. StatefulSet gives the pod stable identity and keeps its storage attached across restarts. Without this, every Chroma pod restart would wipe all ingested documents.

### Why Recreate Strategy for Ollama

Ollama's PVC uses `ReadWriteOnce`, so only one pod can mount it at a time. The default `RollingUpdate` strategy tries to start a new pod before killing the old one, and both pods fight over the same volume — the new pod gets stuck `Pending`. `Recreate` kills the old pod first, then starts the new one:

```yaml
strategy:
  type: Recreate
```

---

## File Structure

```
local-rag-api/
├── app/
│   ├── __init__.py
│   ├── main.py              ← FastAPI routes
│   ├── rag.py                ← RAG pipeline, idempotent ingestion, metadata-aware retrieval, debug search
│   ├── agent.py               ← LangGraph agent, temperature=0
│   ├── ollama_client.py        ← Ollama API calls
│   └── data/
│       └── policy.txt          ← Sample HR policy document
├── eval/
│   ├── ragas_eval.py            ← RAGAS evaluation (covers /ask)
│   └── evaluate_retrieval.py     ← Retrieval-accuracy pass/fail harness
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── chroma-statefulset.yaml
├── ollama-deployment.yaml
├── ollama-service.yaml
├── ollama-model-pull-job.yaml
└── rag-app-deployment.yaml
```

---

## How the Code Files Work Together

### main.py — The Front Door

```
GET  /              → health check
POST /ingest         → ingest a file already inside the container
POST /upload          → upload a PDF, system ingests it (idempotent)
POST /ask              → ask a question, deterministic retrieval, always searches
POST /agent              → ask a question, LLM decides whether to call the search tool
POST /debug-search        → inspect raw retrieval, no generation involved
GET  /documents             → list every document currently indexed
```

### rag.py — The Brain

```
chunk_text()
  → splits raw text into fixed 300-character chunks

content_hash()
  → SHA-256 hash of a document's full text, truncated to 16 hex characters
  → used as a content fingerprint, independent of filename

ingest_document()
  → reads the file (PDF via pypdf, or plain text)
  → computes content_hash(text)
  → checks Chroma for an existing chunk with that content_hash
  → if found: returns immediately with status "skipped_duplicate", no new chunks added
  → if not found: chunks the text, embeds each chunk via Ollama,
    stores chunk + embedding + metadata (source, document_name, chunk_index, content_hash) in Chroma

ask_private_docs()
  → embeds the question
  → queries Chroma for the 3 closest chunks by cosine similarity
  → optionally filtered to one document via document_name
  → builds a prompt, sends it to Llama
  → returns the answer plus a sources list (document, excerpt, relevance score)

debug_search()
  → same retrieval as ask_private_docs(), but stops before generation
  → returns raw chunks, metadata, and scores
  → exists specifically to isolate retrieval failures from generation failures
  → this is the endpoint that caught both idempotency bugs below — it showed
    the raw stored metadata directly, instead of guessing from a confusing API response
```

### agent.py — The Decision-Maker

```
search_documents()   → the tool the agent can choose to call; wraps ask_private_docs()
_last_sources         → module-level global capturing structured source data,
                        since LangChain's @tool functions can only return a string
run_agent()            → invokes the LangGraph agent, returns {answer, sources, tool_was_called}
```

`tool_was_called` is `True` only if the tool actually fired. This exists because an LLM agent has discretion to skip retrieval and answer from its own training data while sounding equally confident either way — this field is the proof that didn't happen.

### ollama_client.py — The Phone

```
embed_text()   → send text to Ollama, get back a vector
ask_llama()     → send a prompt to Ollama, get back an answer
```

### Chroma — The Filing Cabinet

Stores chunks as vectors. A question gets converted to a vector too, and Chroma finds the chunks whose vectors are closest — cosine similarity. Chroma's default distance metric is actually squared L2, not cosine; this collection explicitly overrides that (see below).

---

## Key Engineering Decisions and Bugs Found

### Idempotent Ingestion via content_hash

**The feature:** `ingest_document()` computes a SHA-256 hash of a document's full text (truncated to 16 hex characters) and stores it as `content_hash` in every chunk's metadata. Before chunking, it checks Chroma for any existing chunk with that same hash. If found, it returns immediately with `{"chunks_added": 0, "status": "skipped_duplicate"}` and adds nothing. This means re-uploading the exact same content — even under a different filename — does not duplicate vectors.

**Bug found while testing it — stale records have no hash to match against.** First test re-uploaded a PDF that had already been ingested multiple times across earlier sessions, *before* `content_hash` existed in the code. Expected `skipped_duplicate`; got `chunks_added: 19` instead — a real duplicate add. `/debug-search` confirmed why: the existing chunks in Chroma had no `content_hash` field in their metadata at all, since they predated this code. The duplicate check ran correctly and found nothing to match, because there was genuinely nothing to match against. This is the exact same lesson as the earlier metadata-schema migration, recurring on a different field: changing ingestion code does not retroactively update records already written to the database. Fixed for dev purposes with a volume wipe and clean re-ingest (`docker compose down -v`), which is acceptable for disposable dev data but is explicitly not the production answer — see the versioned-collection note below.

**Bug found immediately after — correct backend result, mislabeled by the API.** After the wipe and re-ingest, uploading the same file a second time correctly added 0 chunks, but the `/upload` response said `"status": "ingested"` instead of `"status": "skipped_duplicate"`. Root cause: `ingest_document()`'s duplicate-skip path does return a `status` key, but `main.py`'s `/upload` route was hardcoding `"status": "ingested"` in its own response dict, never reading `result["status"]` at all. The underlying idempotency logic was correct the whole time; the API layer was just overwriting the truth. Fixed in `main.py` by changing the hardcoded value to `result.get("status", "ingested")` — reads the real status when `ingest_document` provides one, falls back to `"ingested"` for the normal path where no `status` key exists.

**Verified, end to end, with real command output:**
```bash
# First upload of a brand-new document under the new schema
curl -F "file=@Reggie_MGC_Trading_Strategy.pdf" http://localhost:8000/upload
# {"chunks_added": 19, "status": "ingested"}

# Same file, uploaded again immediately
curl -F "file=@Reggie_MGC_Trading_Strategy.pdf" http://localhost:8000/upload
# {"chunks_added": 0, "status": "skipped_duplicate"}
```

**Known, deliberate scope limit — not yet decided either way:** chunk IDs are still `f"{file_path}-{index}"`, not hash-based. If someone edits a file in place and re-ingests under the same filename with different content, the old chunks get silently overwritten rather than versioned. This is a different problem than the one just solved (same content / different filename) and was deliberately left alone rather than expanding scope mid-feature.

### Distance Metric: Cosine vs L2

Chroma defaults to L2 distance, not cosine similarity. Computing `relevance_score = 1 - distance` against raw L2 distances on un-normalized embeddings produced meaningless values — observed in testing as large negative numbers (e.g. -345). Fixed by explicitly setting the metric at collection creation:

```python
collection = client.get_or_create_collection(
    name="private_docs",
    metadata={"hnsw:space": "cosine"}
)
```

This only applies to new collections — Chroma won't change the metric on an existing one in place, which is part of why a metadata schema change later required a fresh collection rather than an in-place fix.

### Generation Inconsistency: Temperature Tuning

With retrieval verified correct and consistent (via `/debug-search` returning the same chunks every run), the agent's final answer still varied across identical repeated questions — sometimes grounded, sometimes inventing detail not present in the source. Root cause: `ChatOllama`'s default non-zero sampling temperature, appropriate for creative generation but wrong for a system whose job is to relay retrieved content faithfully. Fixed with:

```python
llm = ChatOllama(
    model="llama3.1",
    base_url="http://ollama:11434",
    temperature=0
)
```

Confirmed consistent, grounded answers across repeated runs after the change.

### Metadata Schema Migration (original instance, before content_hash)

After adding `document_name` and `chunk_index` to chunk metadata, chunks ingested before that change still carried the old schema (`{"source": file_path}` only) and did not update retroactively — `/debug-search` surfaced this directly, showing `document_name: null` on stale records.

Since this was disposable development data, the practical fix was a volume reset and clean re-ingest:

```bash
docker compose down -v
docker compose up -d --build
```

**This is not the production answer.** In production, never wipe a live vector store. The correct pattern is a versioned collection:

```python
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "private_docs")
collection = client.get_or_create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"}
)
```

with a migration flow of: keep the old collection live → create `private_docs_v2` with the corrected schema → re-index all source documents from S3 → validate via `/debug-search` → cut the app over via the `COLLECTION_NAME` env var → monitor → delete the old collection later.

**Status: this is a stated, designed strategy, not a built one.** The `private_docs_v2` cutover has not actually been implemented or tested. Worth noting this exact pattern has now recurred twice (once for `document_name`/`chunk_index`, once for `content_hash`) — a real, recurring argument for actually building the versioned-collection pattern instead of continuing to wipe dev data each time a schema changes.

### The PORT Environment Variable Conflict

Naming a Kubernetes Service `chroma` on port 8000 causes Kubernetes to auto-inject `PORT=tcp://10.96.149.181:8000` into every pod in the namespace. Chroma's backend expects `PORT` as a plain integer and panics on the injected string:

```
Error loading config: invalid type: found string "tcp://10.96.149.181:8000",
expected u16 for key "PORT"
```

Fix — override explicitly in the StatefulSet env block:

```yaml
env:
- name: IS_PERSISTENT
  value: "TRUE"
- name: PERSIST_DIRECTORY
  value: "/data"
- name: CHROMA_PORT
  value: "8000"
```

Lesson: Kubernetes injects environment variables automatically for service discovery, and generic names (`PORT`, `HOST`, `USER`) can silently collide with what an application expects.

### python-multipart and Why It Breaks in Kubernetes

FastAPI needs `python-multipart` to handle file uploads. It may already exist on a local machine as a side effect of other installed packages, but inside a container, only what's explicitly in `requirements.txt` gets installed. Missing it crashes the upload endpoint with `RuntimeError: Form data requires "python-multipart"`. Lesson: a local machine is not a clean environment — always test with a fresh container before deploying.

### LangGraph Tool Calling Model Requirement

LangGraph agents require a model that supports tool calling. `llama3` (original) does not, and returns `llama3 does not support tools (status code: 400)`. Models that do: `llama3.1`, `llama3.2`, `llama3.3`.

### Memory Requirements for the LangGraph Agent

LangGraph agents make multiple sequential LLM calls per request (reasoning, tool call, synthesis). On an Intel Mac with limited Docker Desktop memory, this caused OOMKill inside a Kind cluster:

```
kind cluster + Ollama + llama3.1 + Chroma + FastAPI + LangGraph
= exceeds available memory on a 7GB Docker allocation
```

Local fix: moved local agent testing to Docker Compose, removing the Kubernetes-in-Docker overhead layer entirely. Production fix (planned, not yet built): EKS with a GPU node (`g4dn.xlarge`).

### Kubernetes DNS — Inside vs Outside the Cluster

Service names (`chroma`, `ollama`) resolve inside the cluster only. From a local machine they don't resolve at all — always port-forward to reach services locally:

```bash
kubectl port-forward svc/chroma 8001:8000
kubectl port-forward deploy/rag-app 8000:8000
```

### Environment Variables vs Hardcoded Paths

```python
# Bad — breaks on container restart, since the path disappears
client = chromadb.PersistentClient(path="./chroma_db")

# Good
import os
CHROMA_PATH = os.getenv("CHROMA_PATH", "/data/chroma_db")
client = chromadb.PersistentClient(path=CHROMA_PATH)
```

Same principle as Terraform variables and Kubernetes ConfigMaps — configuration separated from code, so the same image runs unmodified in every environment.

### async def and Why It Matters

The upload endpoint uses `async def`:

```python
async def upload_file(file: UploadFile = File(...)):
    contents = await file.read()
```

Without async, a large file upload would freeze the server for every other user until it finished. With async, the server can handle other requests while the upload is in flight. One caveat: `ingest_document()` itself is a regular synchronous function and still blocks while it runs. For meaningful concurrent load, it should be pushed to a background thread (`asyncio.run_in_executor`) — not yet done, since current scale doesn't require it.

### YAML Indentation

YAML is whitespace-sensitive, and the most common manifest bug is an `env:` block placed outside the container instead of inside it:

```yaml
# WRONG — env is a sibling of containers, not inside it
spec:
  containers:
  - name: myapp
    image: myapp:latest
  env:
  - name: MY_VAR

# CORRECT
spec:
  containers:
  - name: myapp
    image: myapp:latest
    env:
    - name: MY_VAR
```

### Multi-Document Filtering — Tested for Real

`document_name` filtering existed in code for a while before it was actually exercised with more than one document present. Tested by loading `policy.txt` (via `/ingest`, since it ships baked into the image at `app/data/policy.txt`) alongside the already-ingested `Reggie_MGC_Trading_Strategy.pdf`, then running two real comparisons via `/debug-search`:

1. **No filter, ambiguous-on-purpose question** — "How many vacation days do employees get?" with no `document_name` supplied. Chroma searched across both documents and correctly returned all 3 top chunks from `policy.txt` (relevance scores 0.805, 0.618, 0.532), since that's genuinely the better semantic match. This proves retrieval quality, not filtering.
2. **Filter deliberately pointed at the wrong document** — same question, this time with `document_name: "Reggie_MGC_Trading_Strategy.pdf"` forced in the request. Chroma returned chunks about prop-firm drawdown rules and funded-account comparisons — completely irrelevant to vacation days — with visibly lower relevance scores (0.459, 0.451, 0.448). This is the real proof: if the filter parameter did nothing, this call would have returned the same `policy.txt` chunks as the unfiltered call. Instead it returned different chunks from a different document with worse scores, which only happens if `where={"document_name": ...}` is genuinely restricting Chroma's candidate pool before scoring.

### Retrieval Evaluation Harness — Expanded and Re-Verified

The original `evaluate_retrieval.py` had 2 test cases, both against the trading PDF — a smoke test confirming the harness ran, not real coverage. With `policy.txt` now loaded alongside it, the harness was expanded to 6 cases (3 per document), all run with no `document_name` filter, to test whether unfiltered retrieval naturally routes each question to the correct document on its own:

```
Question 1: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Question 2: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Question 3: PASS (expected policy.txt, got policy.txt)
Question 4: PASS (expected policy.txt, got policy.txt)
Question 5: PASS (expected policy.txt, got policy.txt)
Question 6: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Retrieval Accuracy: 100%
```

Honest scope note: 6 cases across 2 documents is real, meaningful signal — meaningfully stronger than the original 2-case version — but it is not proof this holds at larger document counts or with topically overlapping documents where semantic boundaries get fuzzier. Worth stating that caveat plainly if asked, rather than overselling six passing cases as a comprehensive evaluation.

### Chunking Strategy

Documents are split into fixed 300-character chunks. Too large and Chroma retrieves irrelevant sections that confuse the model; too small and important context gets cut off mid-sentence. 300 characters is a reasonable baseline for short policy documents; legal documents with long clauses likely need 500–800. RAGAS's 0.82 Answer Relevancy score (below) suggests this is worth revisiting — semantic chunking instead of fixed splits is on the roadmap, not yet built.

---

## RAGAS Evaluation Results

Run locally against the sample HR policy corpus, using the system's own local Llama 3 as the judge model — no OpenAI dependency.

| Metric            | Score |
|--------------------|------|
| Faithfulness       | 1.00 |
| Context Precision  | 1.00 |
| Context Recall     | 1.00 |
| Answer Relevancy   | 0.82 |

Faithfulness of 1.00 means every answer was supported by retrieved content, not hallucinated. The 0.82 Answer Relevancy score points at chunking strategy as the next thing worth improving.

**Honest scope note:** this evaluation currently covers `/ask` only, against the HR policy document only. It has not yet been run against `/agent` or the trading-strategy document — that's on the roadmap, not done.

## Retrieval Accuracy (evaluate_retrieval.py)

A separate, smaller harness measuring something different from RAGAS: not generation quality, but whether retrieval routes to the *correct document* at all. Hits `/debug-search` directly and checks the top-returned chunk's `document_name` against an expected value per test question.

Expanded from an initial 2-case smoke test to 6 cases across both real documents (`policy.txt`, `Reggie_MGC_Trading_Strategy.pdf`), all run unfiltered:

```
Question 1: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Question 2: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Question 3: PASS (expected policy.txt, got policy.txt)
Question 4: PASS (expected policy.txt, got policy.txt)
Question 5: PASS (expected policy.txt, got policy.txt)
Question 6: PASS (expected Reggie_MGC_Trading_Strategy.pdf, got Reggie_MGC_Trading_Strategy.pdf)
Retrieval Accuracy: 100%
```

**Honest scope note:** 6 cases across 2 real, topically distinct documents is meaningful evidence the retriever can correctly distinguish between unrelated content. It is not proof this holds at larger document counts, or with documents whose topics genuinely overlap (where semantic boundaries get fuzzier and misrouting becomes more likely). Worth stating that distinction plainly if asked, rather than treating six clean passes as a comprehensive evaluation suite.

---

## API Reference

### `GET /`
```bash
curl http://localhost:8000/
# {"status": "running"}
```

### `POST /upload`
Idempotent — uploading the same content twice returns `chunks_added: 0, status: skipped_duplicate` on the second call.
```bash
curl -F "file=@contract.pdf" http://localhost:8000/upload
# First time:  {"filename": "contract.pdf", "chunks_added": 39, "status": "ingested"}
# Second time: {"filename": "contract.pdf", "chunks_added": 0, "status": "skipped_duplicate"}
```

### `POST /ingest`
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"file_path": "app/data/policy.txt"}'
```

### `GET /documents`
```bash
curl http://localhost:8000/documents
# {"documents": ["policy.txt", "Reggie_MGC_Trading_Strategy.pdf"]}
```

### `POST /ask`
Deterministic RAG. Always searches. Accepts an optional `document_name` to scope retrieval to one file.
```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the termination clauses?", "document_name": "policy.txt"}'
# {
#   "question": "...",
#   "route": "local_ollama_private_rag",
#   "answer": "...",
#   "sources": [{"document": "policy.txt", "excerpt": "...", "relevance_score": 0.91}],
#   "retrieved_chunks": ["..."]
# }
```

### `POST /agent`
LangGraph agent. The LLM decides whether to call the search tool, and can reason across multiple steps.
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

### `POST /debug-search`
Raw retrieval, no generation — for isolating retrieval failures from generation failures.
```bash
curl -X POST http://localhost:8000/debug-search \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the four gates for entry?"}'
# {
#   "query": "...",
#   "results": [{"document_name": "...", "chunk_index": 4, "relevance_score": 0.665, "chunk": "..."}]
# }
```

### Endpoint comparison

```
/ask          → deterministic, always searches documents
/agent        → LLM decides whether/what tool to call, can reason across steps
/debug-search → retrieval only, no LLM generation — for diagnosing where a failure lives
```

---

## How to Run Locally

### Option A — Docker Compose (fastest, recommended for agent testing)

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

### Option B — Kubernetes (Kind)

**Prerequisites:** Docker Desktop, kind, kubectl, Ollama.

```bash
kind create cluster --name rag-dev

docker build -t rag-app:latest .
kind load docker-image rag-app:latest --name rag-dev

kubectl apply -f chroma-statefulset.yaml
kubectl apply -f ollama-deployment.yaml
kubectl apply -f ollama-service.yaml
kubectl apply -f ollama-model-pull-job.yaml
kubectl apply -f rag-app-deployment.yaml

kubectl get pods
# chroma-0     Running
# ollama-xxx   Running
# rag-app-xxx  Running

kubectl port-forward deploy/rag-app 8000:8000
```

Note: the LangGraph agent (`/agent`) is memory-constrained under Kind on machines with limited Docker memory allocation — see Memory Requirements above. Docker Compose is the recommended path for agent testing.

---

## Technology Stack

| Tool | Role |
|------|------|
| FastAPI | API server |
| ChromaDB | Vector database |
| Ollama | Local LLM server |
| Llama 3 | Language model |
| Llama 3.1 / 3.2 | Tool-calling agent model |
| nomic-embed-text | Embedding model |
| LangGraph | Agent framework |
| pypdf | PDF text extraction |
| python-multipart | File upload handling |
| Docker | Containerization |
| Kind | Local Kubernetes |
| Kubernetes | Orchestration |
| RAGAS | RAG evaluation |

---

## What I Built and Learned

### Built
- End-to-end private RAG system from scratch
- File upload endpoint for dynamic ingestion
- Idempotent ingestion via SHA-256 content hashing
- ChromaDB StatefulSet with persistent volume, explicit cosine distance metric
- Ollama with declarative model pulling via init container
- LangGraph agent wrapping RAG as a tool, temperature tuned for faithful generation
- Source citation system on every answer
- Agent grounding verification (`tool_was_called`) to detect silent tool-skip failures
- Metadata-aware retrieval with per-document filtering
- Document registry endpoint
- Retrieval debugging endpoint, decoupled from generation
- RAGAS evaluation pipeline
- A retrieval-accuracy smoke test harness
- Full Kubernetes multi-pod architecture
- Docker Compose path for local agent testing

### Learned
- Kubernetes Deployments vs StatefulSets and when to use each
- Recreate vs RollingUpdate strategy for stateful workloads
- Persistent storage via PVCs and how data survives pod restarts
- Kubernetes DNS and service-to-service communication, and why it doesn't resolve outside the cluster
- Why environment variables can conflict with Kubernetes' own service injection
- Configuration separated from code via environment variables
- LangGraph tool calling and model requirements
- Memory constraints for LLM inference in containerized environments
- RAGAS metrics and what each one actually measures
- Cosine vs L2 distance, and why the metric must be set explicitly at collection creation
- Why changing ingestion code does not retroactively change already-stored vector records, and why this is a recurring pattern (hit twice now), and why production handles it with a versioned collection and re-index rather than a destructive wipe
- Why a correct backend result can still be misreported by an API layer that hardcodes a response field instead of reading what the underlying function actually returned
- Sampling temperature's effect on RAG faithfulness
- Why container filesystem paths must be used instead of local machine paths
- Python syntax fundamentals exercised directly while building this feature: module imports (`import hashlib`), method chaining (`.encode().hexdigest()`), string slicing (`[:16]`), dict literals as query filters (`where={"key": value}`), dict `.get()` with a default value to avoid `KeyError`, list truthiness in `if` checks, and why a missing trailing comma in a multi-line dict produces a misleading `SyntaxError` on the following line

---

## Roadmap

**Done, verified:**
- [x] Local RAG pipeline
- [x] PDF ingestion
- [x] Idempotent ingestion via `content_hash` — built, tested, two real bugs found and fixed, verified with real command output
- [x] Vector search with ChromaDB, cosine metric
- [x] Source citations
- [x] Agent tool telemetry (`tool_was_called`)
- [x] Metadata-aware retrieval (`document_name`, `chunk_index`, `content_hash`)
- [x] Document registry endpoint (`/documents`)
- [x] Retrieval debugging endpoint (`/debug-search`)
- [x] Temperature tuning for generation consistency
- [x] Dockerized, deployed on Kubernetes (Kind), multi-pod
- [x] ChromaDB as StatefulSet with PVC
- [x] Docker Compose for local agent testing
- [x] RAGAS evaluation scoring (on `/ask`, HR policy doc)
- [x] Retrieval-accuracy harness (smoke-test scale, 2 cases)

**Designed, not yet built:**
- [ ] Versioned-collection migration pattern (`private_docs_v2` + `COLLECTION_NAME` env var cutover) — strategy is sound and documented above, implementation not started. Now has two real recurring incidents motivating it (document_name/chunk_index migration, then content_hash migration).
- [ ] Decide on and possibly implement hash-based chunk IDs (currently still `f"{file_path}-{index}"`) — relevant if a file gets edited in place and re-ingested under the same filename
- [ ] Extend RAGAS evaluation to cover `/agent` and the trading-strategy document

**Next, in order, before EKS:**
1. [x] **Retrieval evaluation harness** — `evaluate_retrieval.py` expanded from 2 to 6 test cases across both real documents (`policy.txt`, `Reggie_MGC_Trading_Strategy.pdf`), no `document_name` filter applied on any case. Verified with real output inside the container: 6/6 PASS, 100% accuracy. This is meaningfully stronger evidence than the original 2-case smoke test, since it proves the retriever can correctly distinguish between two unrelated documents on its own, not just confirm a single document trivially matches. Caveat to state honestly if asked: 6 cases across 2 documents is real signal, not proof this holds at larger scale or with topically overlapping documents.
2. [ ] **Better chunking (recursive splitter)** — current chunking is fixed 300-character splits with no awareness of sentence/paragraph boundaries. RAGAS's 0.82 Answer Relevancy score has pointed at this as the likely cause since the very first eval run. Swap in a recursive text splitter (e.g. LangChain's `RecursiveCharacterTextSplitter`) that tries to split on paragraph breaks, then sentences, then words, only falling back to a hard character cut as a last resort. Re-run RAGAS afterward to confirm Answer Relevancy actually improves — don't just assume it will.
3. [ ] **Hybrid search (BM25 + embeddings)** — pure vector/cosine search can miss exact keyword matches (e.g. an exact term, acronym, or proper noun) that a traditional keyword search would catch instantly. Hybrid search runs both BM25 (classic keyword scoring) and the existing embedding search, then combines/re-ranks the results. Worth testing specifically against questions that use exact terminology from the trading strategy doc (e.g. "CHOCH", "LucidFlex") to see whether pure embeddings already handle those well or whether BM25 adds real lift.
4. [ ] **Multi-tool agent** — currently `agent.py` gives the LangGraph agent exactly one tool (`search_documents`). A multi-tool agent would let it choose between multiple capabilities per question — for example, document search vs. a calculator tool vs. a date/time tool — which is the actual test of whether `tool_was_called`-style grounding telemetry holds up when there's a real choice to make, not just a single tool to fire or skip.

**Then, after the above:**
- [ ] EKS deployment with Terraform
- [ ] GPU node for agent inference (`g4dn.xlarge`)
- [ ] S3 storage for raw documents
- [ ] Frontend UI (upload, document selector, question box, sources panel, agent toggle)
- [ ] Reranking
- [ ] LangSmith observability
- [ ] Authentication and RBAC
- [ ] Whisper voice ingestion