# local-rag-api

A private, self-hosted RAG (Retrieval-Augmented Generation) system. Upload a PDF, ask questions, and get grounded answers without sending prompts or documents to a third-party hosted LLM API. It runs locally today and has a documented migration path to Amazon EKS.

Built locally with FastAPI, ChromaDB, Ollama, Llama 3, LangGraph, Docker Compose, and Kubernetes (Kind). Designed next for AWS, EKS, Terragrunt-orchestrated Terraform, GPU inference, GitOps, and MLOps operations.

---

## What This Does

Most AI tools send documents and prompts to a hosted model API. The current system does not: inference, embeddings, retrieval, and vector storage run locally. In the future EKS design, the same workload would run inside the owner's AWS account; the architecture deliberately avoids a third-party hosted LLM API.

```
PDF/TXT Upload
      ↓
FastAPI
      ↓
Idempotency check (content_hash) — skip if already ingested
      ↓
Recursive Chunking (paragraph → sentence → word → character boundaries)
      ↓
Embeddings (nomic-embed-text)
      ↓
ChromaDB (vector storage, cosine similarity, metadata-aware)
      ↓
Semantic Search (optional per-document filter)
      ↓
Llama 3 (Ollama) — generation
Llama 3.1 (Ollama) — agent tool-calling
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
- **Recursive chunking** — splits on paragraph breaks first, then newlines, then sentences, then words, only falling back to raw character cuts as a last resort. Hand-written, zero external dependencies.
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
- **Three evaluation layers:** RAGAS LLM-as-a-judge smoke test (restored in an isolated evaluator environment), retrieval-accuracy harness (`evaluate_retrieval.py`), and custom answer evaluator (`evaluate_answers.py`).
- Kubernetes-native deployment (Kind locally; EKS planned)
- Docker Compose for local development
- Zero external LLM API dependency

---

## Project Status at a Glance — Verified vs. Designed

This project is intentionally documented in three categories. The distinction is important in a technical interview and in production engineering: **verified** means there is real command output or tested behavior; **designed** means the architecture is planned but not yet deployed; **not started** means it belongs on the roadmap, not on a resume as completed work.

| Layer | Current state | Evidence / boundary |
|---|---|---|
| RAG application | **Verified locally** | FastAPI endpoints, PDF/TXT ingestion, Chroma retrieval, Ollama generation, citations, `/debug-search`, and LangGraph agent exist and have been exercised. |
| Retrieval quality | **Diagnosed; next code change pending** | Vector top-3 retrieval missed the detailed Gate 2 and Gate 3 chunks for the four-gate trading question. No hybrid-search or adjacent-chunk code has been deployed yet. |
| Evaluation | **Verified smoke test** | A clean isolated RAGAS environment completed one serial faithfulness test at `1.0000`; the wider suite is still pending. |
| Containers / local orchestration | **Verified** | Docker Compose runs `rag-app`, Chroma, and Ollama. |
| Kubernetes | **Verified only on Kind** | Deployment/StatefulSet/PVC concepts were exercised locally. No Amazon EKS cluster has been created. |
| AWS / EKS / Terragrunt / Terraform / GPU / Karpenter / KEDA / Prometheus / Loki / CloudWatch / Kubeflow | **Designed, not deployed** | These are the next infrastructure and MLOps phases described below. |
| Production security / SLOs / GitOps | **Designed, not deployed** | No claim is made that AWS IAM, network policy, secrets, GitOps, production monitoring, or production HA is complete. |

### Where this project sits on the AI Engineer → MLOps path

```text
AI application engineering                         Platform / MLOps engineering
────────────────────────────────────────────────────────────────────────────────────
[VERIFIED] Ingest → chunk → embed → retrieve       [NEXT] Terragrunt + Terraform → EKS → IAM → GitOps
[VERIFIED] Prompt grounding + citations             [NEXT] GPU scheduling + autoscaling + SLOs
[VERIFIED] RAGAS recovery + retrieval tests         [NEXT] CI promotion gates + model evaluation jobs
[VERIFIED] Docker Compose + Kind learning           [NEXT] observability + security + cost controls
                         └───────────────► One owned system, not unrelated tutorials
```

The application layer is not a toy prerequisite. It gives the infrastructure work a real workload: slow CPU inference, stateful vector data, model images and weights, evaluation jobs, documents that require retention controls, and a measured retrieval defect to fix.

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
│   ├── rag.py                ← RAG pipeline, recursive chunking, idempotent ingestion, metadata-aware retrieval, debug search
│   ├── agent.py               ← LangGraph agent, temperature=0
│   ├── ollama_client.py        ← Ollama API calls (llama3 for generation, llama3.1 for agent tool-calling)
│   └── data/
│       └── policy.txt          ← Sample HR policy document
├── eval/
│   ├── ragas_eval.py             ← RAGAS evaluator; currently configured for a one-question CPU smoke test
│   ├── evaluate_retrieval.py     ← Retrieval-accuracy pass/fail harness (6 cases, 100%)
│   └── evaluate_answers.py       ← Custom answer evaluator (fact presence, citation correctness, insufficient-context detection)
├── eval-env/                     ← Older mixed-generation venv retained as dependency-drift evidence; broken
├── eval-ragas-legacy/            ← Clean isolated local RAGAS environment; do not commit the venv
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
  → recursive splitter: tries paragraph breaks (\n\n) first, then newlines (\n),
    then sentence-ending periods (. ), then spaces, then individual characters
  → hand-written, zero dependencies, replaces the original fixed 300-char slicer

content_hash()
  → SHA-256 hash of a document's full text, truncated to 16 hex characters
  → used as a content fingerprint, independent of filename

ingest_document()
  → reads the file (PDF via pypdf, or plain text)
  → computes content_hash(text)
  → checks Chroma for an existing chunk with that content_hash
  → if found: returns immediately with status "skipped_duplicate", no new chunks added
  → if not found: chunks the text recursively, embeds each chunk via Ollama,
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
embed_text()   → send text to Ollama (nomic-embed-text), get back a vector
ask_llama()     → send a prompt to Ollama (llama3), get back an answer
```

Note: `ask_llama` uses `llama3` (the lighter, faster model) for generation from retrieved context, while `agent.py` uses `llama3.1` via `ChatOllama` for tool-calling decisions. These are separate code paths — generation doesn't need tool-calling support, and llama3 is significantly faster on CPU. See the CPU Inference Latency section below for why this split was necessary.

### Chroma — The Filing Cabinet

Stores chunks as vectors. A question gets converted to a vector too, and Chroma finds the chunks whose vectors are closest — cosine similarity. Chroma's default distance metric is actually squared L2, not cosine; this collection explicitly overrides that (see below).

---

## Key Engineering Decisions and Bugs Found

### Recursive Chunking

**Before:** fixed 300-character slicing with no awareness of text structure. Chunks regularly cut off mid-sentence or mid-word — e.g. `"Gate 1 – 4H/1H Zone\nPrice must be reacting to a defined institutional zone (Order Block, Breaker Block"` ends abruptly, no closing parenthesis, sentence incomplete.

**After:** a hand-written recursive splitter (zero external dependencies) that tries to split on the largest natural boundary first — paragraph breaks (`\n\n`), then single newlines (`\n`), then sentence-ending periods (`". "`), then spaces, then individual characters as a last resort. The function calls itself recursively on any piece that's still oversized after splitting at the current level, working down through smaller separators until everything fits under `chunk_size`.

**Verified improvement via `/debug-search`:** chunks now end on clean boundaries — e.g. `"...maximum of 10 days."` (complete sentence) instead of being cut mid-thought, and `"Gate 4 – 5M OB Retest\nEntry is only taken on the retest of the 5M Order Block created by the displacement candle. No retest, no\nentry."` (complete, self-contained rule). Chunk counts changed from the old fixed-split numbers (policy.txt now produces 4 chunks; trading PDF produces 23 instead of 19) because the splitter sometimes ends chunks earlier to preserve sentence boundaries rather than cramming in extra characters.

**Honest limitation:** the improvement in chunk boundary quality is visually confirmed via `/debug-search` output. The RAGAS evaluator dependency problem has since been recovered in a separate, compatible local environment, but only a one-question faithfulness smoke test has completed so far. The claim remains "chunks are cleaner" (proven), not "all RAGAS metrics improved" (not yet measured).

### CPU Inference Latency and Model Split

**The problem:** after implementing recursive chunking, the `/ask` endpoint began timing out on trading-strategy questions. The `rag-app` container's logs showed a clear `ReadTimeout` after 300 seconds waiting for Ollama to return a response. `docker stats` confirmed Ollama was genuinely working (1365% CPU — all cores saturated), not hung. A direct Ollama diagnostic test confirmed the server was responsive — a trivial "reply OK" prompt on `llama3` completed in 13.6 seconds. The issue was `ollama_client.py` using `llama3.1` (the heavier model) for generation, which exceeded the 300-second timeout on CPU-only inference with a real context-stuffed prompt.

**The fix:** split model usage by purpose. `ollama_client.py`'s `ask_llama()` now uses `llama3` (smaller, faster) for generation from retrieved context — it doesn't need tool-calling support, it just reads a prompt and writes an answer. `agent.py`'s `ChatOllama` stays on `llama3.1` for the agent's reasoning/tool-calling loop, which genuinely requires tool-calling support. These are completely separate code paths that never share a model instance.

**Verified:** the four-gates trading-strategy question (previously timing out after 300 seconds) now completes in ~86 seconds with `llama3`, returning a real, grounded answer with correct source citations. That's still slow (CPU-only inference), but it's working-slow, not broken-slow. This latency is the real, concrete justification for a future EKS GPU inference node. The exact EC2 GPU family must be selected later from current Region availability, model VRAM requirements, quota, and cost—not hard-coded prematurely.

**Also fixed during this investigation:** the old Kind cluster (`rag-dev-control-plane`) was still running from earlier sessions, consuming ~1GB of memory for no reason since all development had moved to Docker Compose. Deleted with `kind delete cluster --name rag-dev`, freeing memory for Ollama.

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

**Status: this is a stated, designed strategy, not a built one.** The `private_docs_v2` cutover has not actually been implemented or tested. Worth noting this exact pattern has now recurred three times (document_name/chunk_index migration, content_hash migration, recursive chunking migration) — a strong, recurring argument for actually building the versioned-collection pattern instead of continuing to wipe dev data each time a schema or chunking strategy changes.

### RAGAS Dependency Drift and Recovery — Resolved for a Controlled Local Smoke Test

**What failed:** RAGAS originally produced real scores (faithfulness 1.00, context precision 1.00, context recall 1.00, answer relevancy 0.82) against the HR policy corpus. Later, the evaluator broke in two different environments after unpinned package updates created mixed LangChain generations:

- **Docker container:** `ragas==0.4.3` with `langchain-community==0.4.2` failed at startup with `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'`.
- **Original local venv (`eval-env/`):** `ragas==0.1.21` and `langchain-community==0.2.19` were paired with modern `langchain-core==1.4.7`, causing `ModuleNotFoundError: No module named 'langchain_core.pydantic_v1'`.

The failure was not in retrieval, Ollama, or the FastAPI API. It was a dependency-generation mismatch: old RAGAS/provider packages expected the pre-0.3 LangChain Core API, while the environment had LangChain Core 1.x.

**Clean recovery:** instead of modifying the running `rag-app` container or patching imports one file at a time, a fresh isolated evaluator venv was created with one internally consistent legacy dependency family:

| Package | Verified version |
|---|---:|
| `ragas` | 0.1.21 |
| `langchain` | 0.2.17 |
| `langchain-core` | 0.2.43 |
| `langchain-community` | 0.2.19 |
| `langchain-openai` | 0.1.25 |

`pip check` returned `No broken requirements found`, and `from ragas import evaluate` completed successfully without any source-code patch.

**Verified end-to-end RAGAS smoke test:** the evaluator ran against the live local `/ask` endpoint using local Ollama as the judge. To avoid overwhelming CPU-only inference, the current `ragas_eval.py` configuration intentionally uses:

```python
eval_questions = eval_questions[:1]

run_config = RunConfig(
    max_workers=1,
    timeout=600,
    max_retries=0,
)

metrics = [faithfulness]
```

Actual result:

```text
Evaluating: 100% | 1/1 [01:55<00:00, 115.82s/it]
{'faithfulness': 1.0000}
```

This proves the restored evaluator can import, call the live API, use Ollama as the judge, and return a RAGAS metric. It does **not** yet prove the full six-question/four-metric suite passes after recursive chunking.

**Why the controlled configuration matters:** a normal `/ask` request can take about 86 seconds on CPU-only Ollama. The first RAGAS attempt launched 20 jobs concurrently and produced `TimeoutError` failures. Limiting evaluation to one question, one worker, and one metric prevents the evaluator from flooding local inference capacity.

**Production lesson:** a container only preserves the dependencies installed when its image was built. A Dockerfile that runs unpinned `pip install` can resolve a different dependency graph on a later rebuild with no application-code changes. The production design remains:

```text
rag-app image
  → FastAPI + retrieval + inference only
  → independently locked application dependencies

rag-eval image
  → RAGAS + evaluation-only packages
  → independently locked dependency graph
  → CI smoke tests: pip check, import ragas, one real evaluation case
  → can fail without taking down the customer-facing API
```

**Next hardening step:** export and commit the exact recovered evaluator dependency graph as a lockfile, then reproduce it in a separate `rag-eval` image. The legacy environment is a validated recovery path, not the permanent production dependency strategy.

**Custom evaluator remains useful:** `eval/evaluate_answers.py` is not a replacement for RAGAS; it complements it by testing domain-specific expected facts, source citations, and insufficient-context behavior directly against `/ask`.

### Four-Gate Retrieval Coverage Gap — Diagnosed, No Production Code Change Yet

A targeted retrieval investigation was run against `Reggie_MGC_Trading_Strategy.pdf` after the answer to **“What are the four gates for entry?”** omitted important gates.

**Current implementation:** `_query_chroma()` embeds the question and asks Chroma for exactly `n_results=3`. Both `/ask` and `/debug-search` use this same helper.

```python
collection.query(
    query_embeddings=[question_embedding],
    n_results=3,
    where={"document_name": document_name},
)
```

**Verified baseline:** the top three semantic results were chunks `4`, `11`, and `7`.

```text
chunk 4  → Four-Gate heading + Gate 1 heading
chunk 11 → unrelated breaker-block text that merely says “same four-gate process”
chunk 7  → end of Gate 3 + Gate 4
```

The required sequence is physically contiguous in the source document:

```text
chunk 4 → Four-Gate heading + Gate 1 heading
chunk 5 → Gate 1 rule + Gate 2 heading
chunk 6 → Gate 2 rule + Gate 3 heading
chunk 7 → Gate 3 rule + Gate 4 rule
```

A read-only test widened semantic retrieval to the top 8. Chunks `5` and `6` were still absent. This rules out the simplistic fix of merely increasing `n_results` from 3 to 5.

A second **read-only BM25 diagnostic** was then run against the already-stored Chroma chunks. No dependency was installed, no application file was edited, and no Docker image was rebuilt. The test compared literal-token BM25 with a narrow `gates → gate` normalization.

```text
Raw BM25 top results:                 4, 2, 3, 15, 11, 7, ...
BM25 with gates → gate normalization: 4, 11, 7, 2, 3, 15, 9, 6
```

Chunk `6` surfaced only at rank 8; chunk `5` still did not rank. The diagnostic showed that keyword retrieval alone does not understand that a numbered procedure continues in neighboring chunks.

**Conclusion:** the proven next retrieval change is **adjacent-chunk expansion**, before or alongside a full hybrid-search implementation.

```text
semantic / lexical retrieval finds a seed chunk (for example, chunk 4)
        ↓
fetch nearby chunk_index values from the same document
        ↓
deduplicate, preserve document order, and enforce a context-size cap
        ↓
pass the complete Gate 1 → 4 sequence to the LLM
```

**Status:** no BM25, reciprocal-rank fusion, or adjacent-chunk-expansion code has been merged into `app/rag.py` yet. The experiments were diagnostics, not a deployment. The future retrieval implementation should be evaluated with the existing `/debug-search` endpoint and retrieval harness before it changes `/ask` behavior.

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

Honest scope note: 6 cases across 2 documents is real, meaningful signal — meaningfully stronger than the original 2-case version — but it is not proof this holds at larger document counts or with topically overlapping documents where semantic boundaries get fuzzier.

---

## Evaluation

### RAGAS Evaluation

#### Current verified local smoke test

RAGAS now runs from the clean `eval-ragas-legacy` environment against the live local `/ask` endpoint and local Ollama judge model.

| Scope | Result |
|---|---|
| Dataset size | 1 question |
| Metrics | Faithfulness only |
| Worker count | 1 |
| Runtime | 115.82 seconds |
| Faithfulness | **1.00** |

A faithfulness score of 1.00 means that, for this one evaluated answer, the local judge found the response fully supported by the retrieved context.

The evaluator is intentionally constrained because CPU-only Ollama cannot safely process the default parallel RAGAS workload. Expand in stages: one additional metric, then 2–3 questions, then the full dataset—while keeping `max_workers=1` locally.

Run the current smoke test:

```bash
eval-ragas-legacy/bin/python -u eval/ragas_eval.py
```

#### Historical broader baseline

Before dependency drift broke the original unpinned environment, RAGAS was run against the sample HR policy corpus:

| Metric            | Score |
|--------------------|------|
| Faithfulness       | 1.00 |
| Context Precision  | 1.00 |
| Context Recall     | 1.00 |
| Answer Relevancy   | 0.82 |

Those historical results covered `/ask` only and the HR policy document only. They are useful baseline evidence, but they are not directly comparable to the current one-question post-recovery smoke test. A full post-recursive-chunking comparison has not yet been completed.

### Retrieval Accuracy (evaluate_retrieval.py)

Measures whether retrieval routes to the *correct document*, independent of generation quality. Hits `/debug-search` directly with no `document_name` filter and checks the top result's document against expected values:

```
6/6 PASS — Retrieval Accuracy: 100%
```

### Custom Answer Evaluator (evaluate_answers.py)

Tests three things RAGAS doesn't cover and that don't require an LLM judge:
- **Expected fact presence** — does the answer contain specific keywords/phrases that must be there for a correct answer?
- **Citation correctness** — does the `sources` field point at the right document?
- **Insufficient-context detection** — for questions the documents genuinely can't answer, does the system correctly say it doesn't know instead of hallucinating?

This is not a RAGAS replacement. It cannot judge faithfulness (whether claims are actually supported by context) or answer relevancy (whether the phrasing addresses the question). It's a blunter, domain-specific check — useful specifically because the expected facts are written by someone who knows the actual document content.

---

## EKS / MLOps Target Architecture — Designed, Not Yet Deployed

This section is the intended production path. It is deliberately concrete enough to implement, but it does **not** claim that the AWS services, GPU nodes, or MLOps control plane are running today.

### Design principles

1. **Keep the source of truth outside the cluster.** Raw documents and evaluation fixtures belong in versioned S3 storage; Chroma is a serving index that can be rebuilt.
2. **Separate stateless API, stateful retrieval, and GPU inference.** They scale, fail, and cost differently.
3. **Keep evaluation isolated from customer traffic.** A bad RAGAS dependency upgrade must block promotion, not break `/ask`.
4. **Use least-privilege workload identity.** No long-lived AWS keys inside container environment variables.
5. **Treat GPU capacity as scarce and cold-start-sensitive.** Scale-to-zero is valuable for batch work, but an interactive assistant needs a latency decision rather than wishful thinking.
6. **Promote by evidence.** A container image is not production-ready merely because it builds; it must pass retrieval, answer, and operational gates.

### Target logical architecture

```text
                                  ┌─────────────────────────────┐
                                  │ GitLab CI                   │
                                  │ lint / test / build / scan  │
                                  │ retrieval + RAGAS gates     │
                                  └──────────────┬──────────────┘
                                                 │ image digest + Git manifest update
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │ Argo CD                     │
                                  │ desired state from Git      │
                                  └──────────────┬──────────────┘
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────┐
│ Amazon EKS                                                                           │
│                                                                                    │
│  Ingress / ALB                                                                      │
│       │                                                                             │
│       ▼                                                                             │
│  rag-api Deployment (CPU) ───────► Chroma StatefulSet (CPU + EBS PVC)              │
│       │                         ▲             │                                   │
│       │                         │             └── EBS snapshot / restore plan      │
│       │                         │                                                 │
│       ├──► S3 (raw docs, versioned corpus, evaluation fixtures)                    │
│       ├──► Secrets Manager (through workload identity / secret sync)               │
│       └──► inference Service ───► GPU inference Deployment                         │
│                                    (Ollama first, vLLM later if justified)         │
│                                                                                    │
│  KEDA: scales queue-driven ingestion/evaluation workers                            │
│  Karpenter: provisions right-sized CPU/GPU EC2 nodes for pending pods              │
│  Prometheus + Grafana: metrics / alerts / capacity evidence                        │
│  CloudWatch or Loki: structured logs; OpenTelemetry later for traces               │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Current-to-target mapping

| Concern | Local, verified today | EKS target state | Why the change matters |
|---|---|---|---|
| API | `rag-app` in Docker Compose | CPU `Deployment` behind an Ingress/ALB | Stateless API can scale independently from GPU inference. |
| Model serving | Ollama on CPU; real `/ask` around 86 seconds | Dedicated GPU inference service; start with Ollama if needed, evaluate vLLM when concurrency/throughput justify it | Moves the known CPU bottleneck off the request path. |
| Vector index | Chroma in Compose / Kind | One Chroma StatefulSet with EBS-backed PVC, snapshots, and re-index plan | Preserves state while treating S3 documents as source of truth. |
| Documents | local upload and `/tmp` working path | versioned S3 corpus with controlled ingestion | Rebuildable, auditable source material. |
| Evaluation | local venv + live API smoke test | separate `rag-eval` image / Kubernetes Job or CI stage | Evaluation failure cannot bring down serving. |
| Infrastructure provisioning | manual local setup / Kind manifests | reusable Terraform modules orchestrated by Terragrunt | repeatable, dependency-aware AWS foundations with isolated environment state. |
| Application delivery | manual Compose and local manifests | GitLab CI + ECR + Argo CD | repeatable image promotion and auditable Kubernetes desired state. |
| Metrics/logs | application logs and local diagnostics | Prometheus/Grafana plus CloudWatch or Loki | measure latency, queue pressure, GPU health, and errors instead of guessing. |
| Identity/security | local development credentials | EKS Pod Identity or IRSA, Secrets Manager, RBAC, NetworkPolicy, image scanning | no AWS keys in pods; reduced blast radius. |

---

### 1. Infrastructure foundation: Terraform modules, Terragrunt environments, then Kubernetes

**Terragrunt does not replace Terraform.** Terraform modules define cloud resources. Terragrunt is the orchestration and environment layer around those modules: it keeps shared configuration DRY, passes environment-specific inputs, configures consistent remote state, and manages dependency order across units.

```text
Terragrunt live configuration
        ↓
Reusable Terraform modules
        ↓
AWS resources: VPC, EKS, ECR, S3, IAM, KMS, security, add-ons
        ↓
Argo CD deploys Kubernetes applications after the cluster exists
```

**Terraform module scope:** VPC/subnets, EKS control plane, ECR, S3 buckets, IAM roles, KMS choices, EKS add-ons, security groups, CloudWatch log groups, and budget/cost tags.

**Terragrunt live-environment scope:** shared remote-state and provider configuration, environment/account/region inputs, dependency outputs, and a safe order such as `vpc → eks → platform add-ons → Karpenter / observability`. It should not be used to deploy the `rag-api` workload itself; Argo CD owns application manifests and continuously reconciles them from Git.

**Target infrastructure repository layout — designed, not created yet:**

```text
infra/
├── modules/                         # reusable Terraform modules
│   ├── vpc/
│   ├── eks/
│   ├── ecr/
│   ├── s3/
│   ├── iam/
│   ├── karpenter/
│   └── observability/
└── live/                            # Terragrunt environment instances
    ├── root.hcl                     # shared remote state, provider generation, common tags
    ├── dev/
    │   └── us-east-1/
    │       ├── vpc/terragrunt.hcl
    │       ├── eks/terragrunt.hcl
    │       ├── platform-addons/terragrunt.hcl
    │       ├── karpenter/terragrunt.hcl
    │       └── observability/terragrunt.hcl
    └── prod/
        └── us-east-1/
            └── ...same unit pattern with production inputs...
```

**State discipline:** each Terragrunt unit gets an isolated remote-state key derived from its environment and path. State must never be shared accidentally across dev and prod. A root `root.hcl` can centralize the backend/provider pattern; child units include it rather than copy/pasting it. The production backend implementation and locking strategy must be chosen and tested before apply, not assumed from a tutorial.

**Dependency discipline:** use a Terragrunt `dependency` block when a downstream unit needs real outputs—such as VPC IDs, private subnet IDs, an EKS cluster endpoint, or an OIDC/Pod Identity-related value. Use ordering-only dependencies when output values are not needed. Dependencies are not magic: a broad `run --all plan` can fail before upstream state exists because Terragrunt cannot resolve outputs. For a first environment, apply foundational units deliberately in order and use carefully scoped planning mocks only when they cannot leak into an apply.

**First deployment shape:**

```text
Terragrunt / Terraform:
VPC + remote state + IAM foundations
        ↓
EKS control plane + system CPU capacity
        ↓
EBS CSI + Pod Identity + metrics/logging base
        ↓
Argo CD
        ↓
rag-api + Chroma
        ↓
GPU inference only after CPU baseline and budgets are proven
```

**Why not start with GPU:** the current system still has an unresolved retrieval coverage defect. A GPU makes wrong or incomplete retrieval faster; it does not repair it. Fix retrieval quality and evaluation first, then use GPU capacity to reduce measured latency.

### 2. Terragrunt workflow and GitOps boundary

The project has two different delivery paths. Keeping them separate is an important operations decision.

```text
Infrastructure path
GitLab CI / merge approval
        ↓
terragrunt hcl fmt / validate / plan
        ↓
reviewed, protected Terragrunt apply
        ↓
AWS foundation changes: VPC, EKS, IAM, ECR, S3, add-ons

Application path
GitLab CI
        ↓
test / evaluate / build / scan
        ↓
push immutable image digest to ECR
        ↓
commit approved image digest to GitOps repository
        ↓
Argo CD reconciles Kubernetes manifests into EKS
```

**Why this separation matters:**

- Terragrunt/Terraform creates and changes **AWS infrastructure**.
- Argo CD deploys and reconciles **Kubernetes workloads**.
- GitLab CI builds, tests, scans, and promotes **application artifacts**.
- Karpenter provisions **EC2 nodes** when Kubernetes has unschedulable pods.
- KEDA adjusts **pod replica counts** from event or queue demand.

A successful `terraform` or `terragrunt` apply does not prove a new model image is safe. A green container build does not justify changing VPC, IAM, or EKS resources. Each plane has its own review and rollback path.

**Terragrunt interview story — designed implementation, not a claimed production incident:**

> “I would model reusable AWS resources as Terraform modules and use Terragrunt to instantiate them per environment. A root Terragrunt configuration would centralize remote state, provider behavior, common tags, and account/region context. The VPC unit exposes subnet outputs to the EKS unit; EKS then exposes the cluster information required by platform add-ons. I would keep those infrastructure dependencies out of application delivery. GitLab CI would validate and plan Terragrunt changes; after approval, a protected apply changes AWS. Separately, Argo CD would reconcile application manifests and image digests into the cluster.”

**Terragrunt failure modes to rehearse before implementation:**

| Scenario | What it means | Evidence | Response |
|---|---|---|---|
| `run --all plan` fails on a dependency output | An upstream unit has never been applied or its state/output is unavailable | Terragrunt error names the dependency/unit | Apply or restore the upstream foundation first; use `mock_outputs` only for controlled plans, never as a substitute for real apply outputs. |
| A dev change appears to target prod state | State key, account, region, or root include configuration is wrong | plan shows unexpected account/resource names or state path | Stop before apply; validate account identity, environment inputs, state key convention, and protected CI environment. |
| Karpenter add-on plan runs before EKS exists | Dependency order is not explicit | missing cluster endpoint/OIDC/add-on APIs | Declare EKS as a dependency and apply the cluster foundation before platform add-ons. |
| A direct `kubectl` change disappears | Argo CD reconciled Git desired state over an out-of-band edit | Argo CD shows OutOfSync then syncs back | Revert or change the GitOps manifest; do not fight the reconciler. |
| Terraform wants to replace a stateful resource unexpectedly | Module input or state drift changed the plan | plan shows replace/destroy actions | Pause; inspect state, lifecycle rules, snapshots, and dependency blast radius before any apply. |

### 3. Compute design: system, CPU, and GPU are different pools

| Node class | Intended workloads | Scheduling policy | Cost/reliability notes |
|---|---|---|---|
| System CPU | CoreDNS, Karpenter, Argo CD, metrics agents | small baseline, protected from application churn | Keep this capacity stable; do not place model workloads here. |
| Application CPU | FastAPI API, Chroma, ingestion helpers | normal CPU requests/limits, anti-affinity where useful | Chroma is stateful and should not be treated like a disposable web pod. |
| GPU | inference server only | GPU taint + matching pod toleration/node selector; request `nvidia.com/gpu` explicitly | No GPU node should exist merely because the cluster is idle. |

A GPU pod must declare the resource it needs. Otherwise Kubernetes and Karpenter do not have enough information to select or create a GPU-capable node.

```yaml
resources:
  requests:
    cpu: "2"
    memory: "12Gi"
    nvidia.com/gpu: "1"
  limits:
    cpu: "4"
    memory: "16Gi"
    nvidia.com/gpu: "1"
```

The exact CPU/memory/GPU values are examples, not final sizing. They must be benchmarked with the selected model, context length, concurrency target, and instance type.

### 4. GPU enablement: NVIDIA Operator, device plugin, and EKS AMI nuance

GPU enablement is a **compatibility decision**, not a one-command installation.

**Chosen learning path for this project:** standard EKS plus self-managed Karpenter. That provides direct experience with scheduling, GPU node constraints, and cost controls.

**Two valid implementation paths:**

1. **EKS-optimized accelerated AMI + NVIDIA device plugin** — simple and AWS-aligned. For Karpenter or EKS Auto Mode, the NVIDIA device plugin remains the compatible baseline.
2. **NVIDIA GPU Operator** — valuable when you want the operator to manage GPU software components and GPU observability such as DCGM exporter. On EKS-optimized AL2023 GPU AMIs, the NVIDIA driver and container toolkit are already present; the Operator must be configured so it does not install competing copies of those components. Do not run a GPU device plugin and an NVIDIA DRA driver for the same GPU on the same node.

**Important current AWS nuance:** NVIDIA DRA is not compatible with Karpenter-provisioned or EKS Auto Mode compute. For this Karpenter learning path, use the NVIDIA device-plugin model rather than DRA.

**First GPU validation checklist:**

```text
1. GPU node launches from the intended NodePool / EC2NodeClass.
2. Node advertises allocatable nvidia.com/gpu.
3. GPU inference pod lands only on GPU nodes.
4. nvidia-smi works inside the inference pod.
5. Model loads without GPU OOM.
6. End-to-end /ask latency is benchmarked against the known CPU baseline.
7. DCGM / GPU memory / utilization metrics are visible before adding autoscaling.
```

**Do not start with GPU time-slicing.** NVIDIA supports GPU sharing/time-slicing, but one exclusive GPU per inference pod is easier to measure and debug. Add sharing only after you have evidence of idle GPU capacity and have tested noisy-neighbor behavior, VRAM contention, and latency impact.

### 5. Karpenter and KEDA: complementary, not interchangeable

```text
KEDA decides: “How many pods should be running for this demand?”
Karpenter decides: “What nodes must exist so those pods can schedule?”
```

| Tool | Job in this project | First use | What it does not solve |
|---|---|---|---|
| KEDA | event-driven **pod** scaling | SQS-backed ingestion or evaluation workers | It does not select EC2 instances or install GPU drivers. |
| Karpenter | right-sized **node** provisioning and consolidation | CPU/GPU pods that cannot schedule | It does not know business queue semantics by itself. |
| HPA | steady scaling for stateless API replicas | `rag-api` based on CPU/concurrency/custom metric | It does not create EC2 nodes. |

**Recommended initial pattern:**

- Keep `rag-api` at `minReplicas: 1` because `/ask` is synchronous and interactive.
- Use KEDA first for **asynchronous** work: document ingestion, batch re-indexing, and scheduled evaluation jobs that pull work from SQS.
- Let Karpenter create GPU nodes when an inference pod requests a GPU and cannot schedule.
- Use NodePool limits, consolidation, expiration, budgets, and tags as cost guardrails.

**Scale-to-zero caveat:** GPU scale-to-zero is not free latency. A cold request may need to wait for EC2 provisioning, node readiness, image pull, and model-weight load. KEDA's HTTP add-on can buffer requests while a backend wakes, but it adds a request-path component and does not erase cold-start time. For an interview assistant with an interactive SLO, choose one of these explicitly:

```text
Option A: keep one small GPU inference replica warm → cost, lower latency
Option B: scale GPU workers to zero → lowest idle cost, higher first-request latency
Option C: API stays warm; requests become async jobs → low idle cost, user polls/gets callback
```

### 6. Stateful retrieval: Chroma, EBS, S3, backups, and migrations

**Initial EKS stateful design:** one Chroma StatefulSet with an EBS-backed PVC and a PodDisruptionBudget appropriate to a single replica. EBS is zonal and `ReadWriteOnce`-oriented, which matches a single-writer Chroma deployment but is not automatic multi-AZ high availability.

**Source-of-truth rule:** Chroma is a derived index. Raw PDFs, normalized extracted text, evaluation questions, and ingestion manifests belong in versioned S3. This makes a collection migration or corruption recovery a controlled re-index rather than a data-loss event.

```text
S3 versioned corpus
      ↓
extract / chunk / embed job
      ↓
private_docs_v2 collection
      ↓
retrieval tests + RAGAS gate
      ↓
COLLECTION_NAME cutover
      ↓
retain old collection until rollback window closes
```

**Backup and recovery requirements before calling this production-ready:**

- EBS CSI driver and snapshot controller installed.
- Scheduled snapshots tested with an actual restore, not just a backup policy.
- A documented re-index job from S3.
- `Retain`/deletion policy decisions documented for production volumes.
- A versioned collection cutover that can roll back without wiping live data.

### 7. Evaluation, model promotion, and Kubeflow boundary

**Now:** RAGAS runs as a constrained local smoke test; the retrieval harness checks top-document routing; the custom evaluator checks required facts, citations, and insufficient-context behavior.

**Before EKS promotion:** package evaluation as a separate `rag-eval` image. CI and/or a Kubernetes Job should run:

```text
1. Python import and dependency checks
2. unit tests and syntax checks
3. retrieval accuracy harness
4. custom answer evaluator
5. bounded RAGAS smoke test
6. optional scheduled broader RAGAS run on dedicated capacity
```

**Promotion gate:** block a new prompt, chunking configuration, embedding model, or serving model if retrieval coverage or grounded-answer tests regress. A successful image build is not enough.

**Kubeflow position:** do **not** install Kubeflow just because the project uses an LLM. Current RAG uses pre-trained models and retrieval; it is not yet a training platform. Kubeflow Pipelines becomes justified when there is real repeated ML workflow work, for example:

```text
source data curation
→ chunking / embedding experiment
→ golden-question evaluation
→ optional embedding fine-tune or QLoRA job
→ safety / schema / regression checks
→ model or adapter artifact registration
→ controlled deployment promotion
```

At that point, Kubeflow provides a containerized DAG, artifact tracking, repeatable parameters, and execution history. Until then, it adds operational weight without solving the current bottleneck.

### 8. Observability: metrics, logs, traces, and alerts

**Metrics: Prometheus + Grafana**

The FastAPI service should expose `/metrics` after adding a Prometheus Python client. Start with metrics that map directly to this project's known failure modes:

| Metric | Why it matters |
|---|---|
| request count, error count, p50/p95/p99 latency | distinguish availability from slowness |
| `/ask` duration split by retrieval and generation | proves whether Chroma or model inference is slow |
| active requests / queue depth | leading signal for capacity pressure |
| retrieved chunk count and document filter usage | catches retrieval regressions |
| ingest duration and duplicate-skip count | observes ingestion behavior |
| RAGAS / retrieval-harness result by build SHA | turns evaluation into a release signal |
| GPU utilization, GPU memory, temperature, XID errors | catches GPU saturation and device failure |
| Karpenter pending-pod / NodePool events | explains capacity and scheduling gaps |

Do not use GPU utilization alone as the autoscaling trigger. Queue depth or request concurrency is a leading indicator; GPU utilization is often a lagging confirmation that users are already waiting.

**Logs: choose deliberately**

- **CloudWatch first:** managed operational simplicity, EKS control-plane log integration, and a fast path to centralized logs.
- **Loki later or in parallel:** useful when you want Grafana-native log exploration and control of log storage/query economics. Loki indexes labels rather than all log content; it still needs careful label cardinality and retention design.

Start application logs as structured JSON with `request_id`, endpoint, deployment SHA, document name/hash where safe, retrieval chunk IDs, model name, and latency. Never log raw private document content, secrets, or full prompts by default.

**Traces: later but planned**

Add OpenTelemetry when the system has separate API, retrieval, inference, and queue services. A trace should show:

```text
HTTP request → retrieval → Chroma query → prompt build → inference call → response
```

This is how you prove where a slow request spent time rather than guessing from one aggregate latency number.

**Initial alerts:**

```text
- p95 /ask latency above SLO for sustained window
- error-rate spike
- GPU pod pending beyond threshold
- GPU memory exhaustion / XID device event
- Chroma PVC unavailable or snapshot failure
- queue depth rising while worker count is flat
- RAGAS/retrieval promotion gate failure on main branch
- Karpenter cannot provision allowed capacity
```

### 9. Identity, secrets, network security, and supply chain

**Workload IAM:** use **EKS Pod Identity** as the default design for AWS service access where it fits this project; retain IRSA knowledge because some existing add-ons and organizations still use it. Each Kubernetes service account gets only the AWS permissions it needs.

| Workload | Example minimum AWS access |
|---|---|
| `rag-api` | read approved S3 document prefix; read specific secret values if needed |
| `ingest-worker` | read/write designated ingestion prefixes; publish status event if used |
| `rag-eval` | read evaluation corpus/results bucket; no production document write permission |
| log/metrics add-ons | their own scoped delivery permissions |
| Karpenter controller | only the AWS permissions required to launch/tag/manage allowed compute |

**Security baseline:**

- Private subnets for worker nodes; only the load balancer is public where needed.
- EKS access entries / RBAC for human access; no shared cluster-admin credential.
- Secrets in AWS Secrets Manager or Parameter Store, synchronized or mounted through a controlled mechanism; never committed in Git or baked into images.
- EBS/S3 encryption and KMS policy design; TLS at the edge and for sensitive service paths where required.
- Namespace boundaries, default-deny NetworkPolicies, then explicit allows: API → Chroma, API → inference, DNS, and scoped AWS egress.
- Pod hardening: run as non-root, drop Linux capabilities, `allowPrivilegeEscalation: false`, `seccompProfile: RuntimeDefault`, resource limits, and read-only filesystem where the image supports it.
- Image scanning and SBOM in CI; signed/immutable image digests promoted through environments.
- Audit EKS control-plane logs and GitOps changes. Do not make direct `kubectl apply` the production deployment path.

### 10. GitOps / CI-CD flow

```text
Infrastructure pull request
   ↓
GitLab CI: terragrunt hcl fmt / validate / plan
   ↓
review plan + protected environment approval
   ↓
Terragrunt apply: Terraform modules create/update AWS foundation
   ↓
EKS / IAM / ECR / S3 / add-ons are available

Application commit
   ↓
GitLab CI
   ├─ format / lint / unit tests
   ├─ retrieval and answer-evaluator tests
   ├─ bounded RAGAS gate
   ├─ build immutable image
   ├─ scan image + produce SBOM
   └─ push image to ECR by digest
   ↓
Update GitOps environment manifest repository with the approved image digest
   ↓
Argo CD detects Git change and reconciles EKS
   ↓
Deployment health + smoke test + metrics check
   ↓
Promote or roll back through Git history
```

**Rollout discipline:** begin with a separate `dev` namespace/account, then staging, then production. Use a deployment strategy appropriate to the service. For model/prompt changes, a canary or shadow evaluation is more meaningful than a blind rolling update because quality can regress while Kubernetes health remains green.

**Argo CD nuance:** automated sync is useful for drift correction, but deployment rollback must be designed around the Git desired-state model. A revert to the known-good manifest/image digest is the primary rollback path; do not rely on ad-hoc cluster edits that Argo CD will overwrite.

### 11. Cost controls before any AWS apply

No EKS resources need to be created while the application and retrieval work are still being validated locally. The immediate cost-control plan is:

```text
Now (zero AWS spend)
  - finish retrieval correctness and local evaluation
  - write Terraform; run fmt/validate/plan only
  - create manifests, policies, and runbooks in Git

Before first apply
  - AWS Budget + alert
  - required tags: project, environment, owner, cost-center
  - region and GPU quota check
  - Karpenter NodePool limits
  - no always-on GPU baseline by default
  - TTL / scheduled teardown plan for non-production clusters
  - CloudWatch/Loki retention limits

After first apply
  - benchmark CPU-vs-GPU latency and cost per successful answer
  - decide whether a warm GPU replica is worth the interactive latency benefit
  - delete the environment after the learning window if it is not actively used
```

A GPU node that is always on while there is no traffic is not a platform achievement; it is an unmeasured expense.

### 12. EKS and Terragrunt troubleshooting playbook (expected issues, evidence, response)

| Symptom | Likely cause | First evidence to collect | Correct response |
|---|---|---|---|
| GPU pod stays `Pending` | missing `nvidia.com/gpu` request, GPU taint/toleration mismatch, NodePool constraint too narrow, quota/capacity issue | pod events, NodePool/NodeClaim status, node allocatable resources | fix scheduling contract before changing model code |
| GPU node exists but pod sees no GPU | device plugin/operator/AMI mismatch | `kubectl describe node`, plugin logs, `nvidia-smi` in pod | use one compatible driver/plugin path; avoid duplicate driver/toolkit installation |
| GPU pod starts then exits | model too large for VRAM, host RAM OOM, bad model artifact | pod termination reason, GPU memory metric, container logs | lower model/context/concurrency or select a larger compatible GPU |
| First request is very slow after idle | node, image, or model cold start | trace timestamps: scale trigger → node → pod → model ready | use warm capacity, pre-pulled image/model cache, or asynchronous workflow |
| KEDA sees backlog but no capacity arrives | scaler auth/config failure or pending pod cannot schedule | KEDA events, queue metric, pending pod events, Karpenter logs | separate “pod scale failed” from “node scale failed” |
| Chroma PVC stays pending | EBS CSI / StorageClass / AZ topology/IAM issue | PVC events, CSI controller logs, node zone | fix storage class, CSI identity, and zone-aware scheduling |
| Liveness probe restarts a healthy app | liveness test depends on slow/downstream Ollama or Chroma | kubelet events, probe logs | liveness = process alive; readiness = dependencies able to serve |
| `/ask` slow but cluster healthy | too much context, model queue, CPU/GPU saturation, retrieval/inference bottleneck | request trace, prompt size, GPU/CPU metrics | optimize the measured segment; do not scale blindly |
| retrieval answer incomplete | top chunks omit required adjacent sequence | `/debug-search`, retrieval harness, chunk indices | implement and test adjacent-chunk expansion before blaming the LLM |
| evaluator breaks after rebuild | dependency drift | `pip check`, import smoke test, lockfile diff | isolate evaluator image and promote only locked dependency changes |

---

### Interview-ready project narrative

> I built the application layer locally first so that the MLOps work has a real workload behind it. The system ingests private documents, chunks and embeds them, stores vectors in Chroma, retrieves grounded context, returns citations, and has deterministic retrieval debugging. I found and fixed issues in idempotent ingestion, metadata migration, distance metric selection, CPU inference timeout, and RAGAS dependency drift. I then proved a retrieval coverage defect: the four-gate answer was incomplete because the relevant numbered procedure was split across neighboring chunks that top-k semantic retrieval never returned. The next application change is adjacent-chunk expansion, measured through the existing retrieval and answer-evaluation harnesses.
>
> For the EKS phase, I would separate the CPU API, stateful vector index, GPU inference, and evaluation jobs. I would define reusable AWS resources in Terraform modules and orchestrate dev/stage/prod instances with Terragrunt, using shared remote-state/provider configuration and explicit foundation dependencies. GitLab CI would validate and plan infrastructure changes; Argo CD would separately promote application manifests and immutable image digests. I would use EKS Pod Identity for least-privilege access, Karpenter for node capacity, KEDA for asynchronous workers, Prometheus/Grafana for metrics, and CloudWatch or Loki for logs. I would not install Kubeflow until the project has a real repeatable training or fine-tuning workflow. The key is that every infrastructure decision maps to a measured application problem—not a tool checklist.

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
```

### `POST /agent`
LangGraph agent. The LLM decides whether to call the search tool, and can reason across multiple steps.
```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the four gates for entry?"}'
```

### `POST /debug-search`
Raw retrieval, no generation — for isolating retrieval failures from generation failures.
```bash
curl -X POST http://localhost:8000/debug-search \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the four gates for entry?"}'
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
| Llama 3 | Language model (generation) |
| Llama 3.1 | Tool-calling agent model |
| nomic-embed-text | Embedding model |
| LangGraph | Agent framework |
| pypdf | PDF text extraction |
| python-multipart | File upload handling |
| Docker | Containerization |
| Terraform | Reusable AWS infrastructure modules (designed EKS phase) |
| Terragrunt | Environment orchestration, shared configuration, dependency order, and state conventions around Terraform (designed EKS phase) |
| Kind | Local Kubernetes |
| Kubernetes | Orchestration |
| RAGAS | LLM-as-a-judge RAG evaluation (isolated local smoke test restored; separate eval image planned) |

---

## What I Built and Learned

### Built
- End-to-end private RAG system from scratch
- File upload endpoint for dynamic ingestion
- Idempotent ingestion via SHA-256 content hashing
- Recursive chunking (hand-written, zero dependencies, respects paragraph/sentence/word boundaries)
- ChromaDB StatefulSet with persistent volume, explicit cosine distance metric
- Ollama with declarative model pulling via init container
- LangGraph agent wrapping RAG as a tool, temperature tuned for faithful generation
- Model split: llama3 for fast generation, llama3.1 for agent tool-calling only
- Source citation system on every answer
- Agent grounding verification (`tool_was_called`) to detect silent tool-skip failures
- Metadata-aware retrieval with per-document filtering
- Document registry endpoint
- Retrieval debugging endpoint, decoupled from generation
- Three evaluation layers: restored RAGAS local smoke test, retrieval-accuracy harness, custom answer evaluator
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
- CPU inference latency as a real, measurable bottleneck — diagnosed with `docker stats`, direct Ollama timing tests, and FastAPI log tracebacks, leading to a concrete model-split fix
- RAGAS metrics and what each one actually measures
- Cosine vs L2 distance, and why the metric must be set explicitly at collection creation
- Why changing ingestion code does not retroactively change already-stored vector records — hit three times now, recurring argument for versioned-collection migrations
- Why a correct backend result can still be misreported by an API layer that hardcodes a response field instead of reading what the underlying function actually returned
- Sampling temperature's effect on RAG faithfulness
- Why container filesystem paths must be used instead of local machine paths
- **Dependency management as a real production concern:** unpinned `pip install` silently broke RAGAS in both the Docker container and the local venv at different times, with zero application-code changes. The recovery was a clean evaluator environment with a consistent RAGAS/LangChain generation, verified by `pip check`, an import smoke test, and a real RAGAS faithfulness run. Production-correct hardening is a committed lockfile with exact versions and hashes plus a separate evaluator image.
- Python syntax fundamentals exercised directly while building features: module imports, method chaining, string slicing, dict literals as query filters, dict `.get()` with defaults, list truthiness in conditionals, recursive function calls, `any()` with generator expressions, list comprehensions, tuple unpacking with `_` discard
- Why top-k semantic retrieval can miss a contiguous, numbered procedure even when every source chunk is stored correctly; diagnosis came from `/debug-search`, an expanded semantic query, and a read-only BM25 experiment—not from guessing.
- The professional difference between a verified local feature, a measured design, and a production deployment claim.
- How Terragrunt complements—not replaces—Terraform: Terraform modules define resources; Terragrunt keeps multi-environment configuration DRY, structures remote state, carries environment inputs, and coordinates dependency-aware infrastructure runs. This is designed for the EKS phase and is not claimed as deployed.

---

## Roadmap

### Phase 0 — Preserve the verified local baseline

- [x] Save the recovered RAGAS environment snapshot as `requirements-ragas-legacy.txt`.
- [x] Verify one serial RAGAS faithfulness run against the live local API: `1.0000`.
- [x] Keep `rag-app` separate from evaluation dependencies.
- [ ] Commit the lockfile and add a short reproduction command to the repository.
- [ ] Replace broad/unpinned application requirements with reviewed, pinned dependency inputs and generated locks.
- [ ] Build `Dockerfile.eval` / `rag-eval` image and CI smoke checks: `pip check`, RAGAS import, one bounded evaluation.

### Phase 1 — Retrieval correctness before infrastructure spend

- [x] Prove the four-gate failure is a retrieval-coverage issue, not an LLM-only issue.
- [ ] Implement adjacent-chunk expansion with same-document boundaries, dedupe, deterministic order, and a final context-size cap.
- [ ] Add a regression test asserting the four-gate query returns chunks `4–7` or their equivalent content.
- [ ] Decide whether BM25/hybrid retrieval is still needed after adjacent expansion; if yes, implement it as a measured experiment, not a blind library add.
- [ ] Add topically overlapping documents to the retrieval test corpus.
- [ ] Expand RAGAS gradually: one more metric → 2–3 questions → full suite, retaining `max_workers=1` locally.
- [ ] Add `/health` for liveness and `/ready` for dependency readiness; do not make liveness depend on a slow model call.

### Phase 2 — Make the application deployable and observable

- [ ] Add structured JSON logging and request IDs.
- [ ] Add Prometheus `/metrics` instrumentation and a local Grafana dashboard.
- [ ] Track retrieval, generation, ingestion, and evaluator metrics separately.
- [ ] Build slim, pinned images for API, evaluation, and later inference.
- [ ] Add unit tests around chunking, duplicate detection, source construction, and adjacent expansion.
- [ ] Add image scanning, SBOM generation, and dependency scanning to CI.
- [ ] Move raw document storage and reproducible corpus fixtures to versioned S3 in the AWS phase.

### Phase 3 — Terragrunt/Terraform EKS foundation with no GPU dependency

- [ ] Terraform modules + Terragrunt live configuration: VPC, private subnets, EKS, ECR, S3, IAM, EBS CSI, CloudWatch logs, KMS decisions, tags, budget alarms, remote-state design, and environment isolation.
- [ ] Choose standard EKS + self-managed Karpenter as the hands-on learning path; document EKS Auto Mode as a lower-operations alternative.
- [ ] Install Argo CD and deploy CPU-only `rag-api` + Chroma via GitOps; keep Terragrunt/Terraform focused on AWS and platform foundations.
- [ ] Use EKS Pod Identity/IRSA design, least-privilege service accounts, Secrets Manager, NetworkPolicies, and RBAC.
- [ ] Use EBS-backed Chroma PVC with snapshot/restore test and versioned collection migration plan.
- [ ] Add Prometheus/Grafana and choose CloudWatch-first or Loki-first logging deliberately.
- [ ] Run the same retrieval and evaluation gates in EKS before adding GPU.

### Phase 4 — GPU inference and autoscaling

- [ ] Benchmark the current CPU baseline against one GPU-serving option using a real model, real context length, and real concurrency.
- [ ] Add a GPU NodePool with taints, explicit `nvidia.com/gpu` requests, Karpenter limits, and cost tags.
- [ ] Select one compatible GPU software path: EKS accelerated AMI + device plugin, or a correctly configured NVIDIA GPU Operator.
- [ ] Validate DCGM/GPU metrics, VRAM limits, GPU OOM behavior, and cold-start time.
- [ ] Split CPU API from GPU inference service; decide whether Ollama remains adequate or vLLM is warranted.
- [ ] Use KEDA for SQS-backed ingestion/evaluation workers; keep interactive API availability separate from batch scale-to-zero goals.
- [ ] Add Karpenter consolidation/expiry controls only after PodDisruptionBudgets and stateful behavior are understood.

### Phase 5 — MLOps maturity when there is a real training loop

- [ ] Create a versioned golden evaluation dataset and promotion policy for prompts, chunking, embedding models, and LLMs.
- [ ] Add model/prompt canary or shadow evaluation before production promotion.
- [ ] Add OpenTelemetry traces across API → retrieval → inference.
- [ ] Introduce MLflow/model registry only when models/adapters need lifecycle management.
- [ ] Introduce Kubeflow Pipelines only when repeated fine-tuning, embedding experiments, or scheduled ML workflows justify its operational cost.
- [ ] Consider QLoRA adapters, reranking, and model serving changes after measured evaluation evidence—not before.

### Technical references for the planned EKS phase

These are design references, not evidence that the corresponding component is already deployed in this project.

- [Terragrunt overview: shared includes and DRY configuration](https://terragrunt.gruntwork.io/docs/getting-started/overview/)
- [Terragrunt state backend and path-based state isolation](https://terragrunt.gruntwork.io/docs/features/state-backend/)
- [Terragrunt dependency blocks and output handling](https://terragrunt.gruntwork.io/docs/reference/config-blocks-and-attributes)
- [Amazon EKS GPU-optimized AMIs and GPU Operator interaction](https://docs.aws.amazon.com/eks/latest/userguide/ml-eks-optimized-ami.html)
- [Amazon EKS GPU device management and Karpenter/DRA compatibility](https://docs.aws.amazon.com/eks/latest/userguide/device-management.html)
- [NVIDIA GPU Operator overview](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/index.html)
- [Karpenter NodePools and disruption/consolidation](https://karpenter.sh/docs/concepts/nodepools/)
- [KEDA event-driven workload scaling](https://keda.sh/docs/2.21/concepts/)
- [KEDA HTTP add-on and scale-to-zero behavior](https://keda.sh/http-add-on/0.15/)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Amazon EBS CSI driver and persistent volumes](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html)
- [EBS CSI snapshot controller](https://docs.aws.amazon.com/eks/latest/userguide/csi-snapshot-controller.html)
- [Amazon EKS observability and CloudWatch](https://docs.aws.amazon.com/eks/latest/userguide/cloudwatch.html)
- [Prometheus instrumentation client libraries](https://prometheus.io/docs/instrumenting/clientlibs/)
- [Grafana Loki architecture](https://grafana.com/docs/loki/latest/get-started/architecture/)
- [Amazon EKS network security practices](https://docs.aws.amazon.com/eks/latest/best-practices/network-security.html)
- [Kubeflow Pipelines overview](https://www.kubeflow.org/docs/components/pipelines/overview/)
- [Argo CD automated sync and GitOps behavior](https://argo-cd.readthedocs.io/en/latest/user-guide/auto_sync/)


