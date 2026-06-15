# local-rag-api

A private RAG (Retrieval Augmented Generation) system that runs completely on your machine. Upload any PDF, ask questions, get answers — zero data leaving your infrastructure.

Built with FastAPI, ChromaDB, Ollama, and Kubernetes.

---

## What This Does

Most AI tools send your data to OpenAI or Anthropic. This system does not.

Everything runs locally:

```
You upload a PDF
        ↓
FastAPI receives it
        ↓
ChromaDB stores the knowledge
        ↓
Ollama (Llama3) answers your questions
        ↓
Zero data leaves your machine
```

Real use cases:
- Law firm uploads case files and asks questions across all documents
- Healthcare client uploads compliance policies and queries them privately
- Any organization with sensitive documents they cannot send to the cloud

---

## Architecture

```
User
  ↓
POST /upload or /ask
  ↓
FastAPI Deployment (rag-app)
  ↓              ↓
Chroma        Ollama
StatefulSet   Deployment
  ↓
PVC Storage (5GB persistent)
```

Three separate pods talking to each other by Kubernetes service name:

| Pod | Role | Type |
|-----|------|------|
| rag-app | FastAPI API server | Deployment |
| chroma-0 | Vector database | StatefulSet |
| ollama | LLM server (Llama3) | Deployment |

---

## Why StatefulSet for Chroma

Kubernetes has two ways to run pods:

**Deployment** — for stateless apps. Pods are interchangeable. If one dies, a new one starts fresh. Good for your FastAPI server.

**StatefulSet** — for stateful apps. Pods have stable identity and storage attached to them. If the pod dies, the new one reconnects to the same storage. Required for Chroma because Chroma stores data on disk.

If Chroma ran as a Deployment without a PVC, every pod restart would wipe your ingested documents.

---

## File Structure

```
local-rag-api/
├── app/
│   ├── __init__.py
│   ├── main.py            ← FastAPI routes (front door)
│   ├── rag.py             ← RAG logic (the brain)
│   ├── ollama_client.py   ← Calls Ollama (the phone)
│   └── data/
│       └── policy.txt     ← Sample document
├── eval/
│   └── ragas_eval.py      ← Accuracy scoring
├── Dockerfile
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

Three endpoints anyone can call:

```
GET  /         → health check, is the server alive?
POST /upload   → send a PDF, system ingests it
POST /ask      → send a question, get an answer
```

When you run `curl -X POST http://localhost:8000/ask` you are knocking on the `/ask` door in `main.py`.

### rag.py — The Brain

Does the actual work:

```
ingest_document()
  → reads the file
  → cuts it into chunks (300 characters each)
  → sends each chunk to Ollama for embedding
  → stores chunks + embeddings in Chroma

ask_private_docs()
  → converts your question to numbers (embedding)
  → asks Chroma: find me the 3 most similar chunks
  → sends chunks + question to Ollama
  → returns answer + the chunks used
```

### ollama_client.py — The Phone

Two functions. That is all it does:

```
embed_text()  → send text to Ollama, get back numbers (embedding)
ask_llama()   → send a prompt to Ollama, get back an answer
```

### Chroma — The Filing Cabinet

Stores chunks as vectors (numbers). When you ask a question, Chroma converts the question to numbers and finds the chunks with the closest matching numbers. That is cosine similarity. You do not need to code it. You just need to know that is what is happening.

---

## What python-multipart Is and Why It Matters

FastAPI uses a package called `python-multipart` to handle file uploads.

When you upload a file over HTTP, it travels as `multipart/form-data`. That means the file is split into labeled parts:

```
part 1 → filename: contract.pdf
part 2 → content-type: application/pdf
part 3 → actual file bytes
```

FastAPI needs `python-multipart` to decode those parts back into a usable file.

**Why this breaks Kubernetes specifically:**

On your Mac, when you run `pip3 install fastapi`, other packages get installed automatically as side effects. `python-multipart` might already be on your machine from something else.

Inside a Docker container, only what is in `requirements.txt` gets installed. Nothing extra. So if `python-multipart` is not in your `requirements.txt`, the container starts without it, the upload endpoint crashes, and you see:

```
RuntimeError: Form data requires "python-multipart"
```

The fix is always the same:

```
echo "python-multipart" >> requirements.txt
docker build -t rag-app:latest .
kind load docker-image rag-app:latest --name rag-dev
kubectl rollout restart deployment/rag-app
```

**The lesson:** Your local machine is not a clean environment. Docker containers are. Something that works locally can fail in Kubernetes if it is not explicitly in `requirements.txt`.

---

## The PORT Environment Variable Conflict

When you deploy Chroma to Kubernetes, you may see this crash in the logs:

```
Error loading config: invalid type: found string "tcp://10.96.149.181:8000",
expected u16 for key "PORT"
```

**What happened:**

Kubernetes automatically creates environment variables for every service in the namespace. If your Chroma service is named `chroma` and runs on port 8000, Kubernetes injects:

```
PORT=tcp://10.96.149.181:8000
```

Chroma's Rust backend expects `PORT` to be a plain number like `8000`. It receives the full TCP string and panics.

**The fix:**

Override `PORT` explicitly in the StatefulSet env block:

```yaml
env:
- name: IS_PERSISTENT
  value: "TRUE"
- name: PERSIST_DIRECTORY
  value: "/data"
- name: CHROMA_PORT
  value: "8000"
```

**The lesson:** Kubernetes injects environment variables automatically for service discovery. If your application uses a variable name that conflicts with one Kubernetes injects (`PORT`, `HOST`, `USER`), it will break in ways that only appear inside the cluster, never locally.

---

## YAML Indentation Rules

YAML is whitespace-sensitive. One wrong indent breaks everything.

The rule for Kubernetes manifests:

```yaml
containers:
- name: myapp           ← container starts here with a dash
  image: myapp:latest   ← two spaces from the dash
  ports:                ← same level as image
  - containerPort: 8000
  env:                  ← same level as ports, NOT under spec
  - name: MY_VAR        ← one level under env
    value: "hello"      ← same level as name
```

Common mistake — env block placed outside the container:

```yaml
# WRONG
    spec:
      containers:
      - name: myapp
        image: myapp:latest
      env:               ← this is outside the container
      - name: MY_VAR

# CORRECT
    spec:
      containers:
      - name: myapp
        image: myapp:latest
        env:             ← this is inside the container
        - name: MY_VAR
```

---

## async def and Why It Matters

Your upload endpoint uses `async def`:

```python
async def upload_file(file: UploadFile = File(...)):
    contents = await file.read()
```

**What this means in plain English:**

Without async, uploading a large file freezes the entire server for every other user until that upload finishes. One user blocks everyone.

With async, the server says: while this file is uploading, go handle other requests. When the file finishes, come back and continue.

```
Without async:
User A uploads 50MB PDF → server frozen → User B waits
User B asks a question  → no response until A finishes

With async:
User A uploads 50MB PDF → server continues
User B asks a question  → answered immediately
User A upload finishes  → processing continues
```

**The one thing to watch:**

`ingest_document()` is a regular synchronous function. It still blocks while it runs. For production with many concurrent users, wrap it:

```python
import asyncio
result = await asyncio.run_in_executor(None, ingest_document, temp_path)
```

That pushes the blocking work to a background thread. For current scale this is not required.

---

## Environment Variables in Code

Bad — hardcoded paths:

```python
client = chromadb.PersistentClient(path="./chroma_db")
```

This breaks in Kubernetes because `./chroma_db` means inside the container filesystem, which disappears on restart.

Good — environment variable with fallback:

```python
import os
CHROMA_PATH = os.getenv("CHROMA_PATH", "/data/chroma_db")
client = chromadb.PersistentClient(path=CHROMA_PATH)
```

Now the same code works everywhere:

```
Local dev:    CHROMA_PATH=./chroma_db
Kubernetes:   CHROMA_PATH=/data/chroma_db
Production:   connects to Chroma service via HttpClient
```

This is the same principle as Terraform variables and Kubernetes ConfigMaps. Configuration separated from code. You already knew this from DevOps. Now it applies to application code.

---

## Kubernetes DNS — Inside vs Outside the Cluster

Services inside Kubernetes get a DNS name:

```
chroma     → resolves to the Chroma pod IP inside the cluster
ollama     → resolves to the Ollama pod IP inside the cluster
```

Your FastAPI code uses:

```python
client = chromadb.HttpClient(host="chroma", port=8000)
```

This works because FastAPI runs inside the cluster and can resolve `chroma` by DNS.

Your Mac cannot resolve `chroma`. It has no idea what that hostname means.

To access services from your Mac, you must port-forward:

```bash
kubectl port-forward svc/chroma 8001:8000
# Now localhost:8001 on your Mac maps to chroma:8000 inside the cluster
```

Always port-forward before testing from your Mac. Never assume a service hostname works outside the cluster.

---

## The Chunking Strategy

Your documents get split into 300-character chunks:

```python
def chunk_text(text: str, chunk_size: int = 300) -> list:
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
```

Why chunks matter for answer quality:

- Too large → Chroma retrieves irrelevant sections along with the right ones, confusing the model
- Too small → important context gets cut off mid-sentence
- 300 characters → good starting point for HR policy documents

For legal documents with long clauses, increasing to 500-800 characters often improves results. This is where RAGAS evals help — you can measure the impact of changing chunk size before deploying to clients.

---

## How to Run Locally

**Prerequisites:**
- Docker Desktop
- kind
- kubectl
- Ollama

**1. Create the cluster:**

```bash
kind create cluster --name rag-dev
```

**2. Pull models:**

```bash
ollama pull llama3
ollama pull nomic-embed-text
```

**3. Build and load the image:**

```bash
docker build -t rag-app:latest .
kind load docker-image rag-app:latest --name rag-dev
```

**4. Deploy everything:**

```bash
kubectl apply -f chroma-statefulset.yaml
kubectl apply -f ollama-deployment.yaml
kubectl apply -f ollama-service.yaml
kubectl apply -f ollama-model-pull-job.yaml
kubectl apply -f rag-app-deployment.yaml
```

**5. Verify:**

```bash
kubectl get pods
# chroma-0     Running
# ollama-xxx   Running
# rag-app-xxx  Running
```

**6. Port forward and test:**

```bash
kubectl port-forward deploy/rag-app 8000:8000
```

New terminal:

```bash
# Upload a document
curl -F "file=@/path/to/your/document.pdf" http://localhost:8000/upload

# Ask a question
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the main points?"}'
```

---

## API Reference

### GET /
Health check.

```bash
curl http://localhost:8000/
# {"status": "running"}
```

### POST /upload
Upload a PDF and ingest it into Chroma.

```bash
curl -F "file=@contract.pdf" http://localhost:8000/upload
# {"filename": "contract.pdf", "chunks_added": 39, "status": "ingested"}
```

### POST /ask
Ask a question against all ingested documents.

```bash
curl -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the termination clauses?"}'
# {
#   "question": "What are the termination clauses?",
#   "route": "local_ollama_private_rag",
#   "answer": "...",
#   "retrieved_chunks": ["..."]
# }
```

---

## Stack

| Tool | Role |
|------|------|
| FastAPI | API server |
| ChromaDB | Vector database |
| Ollama | Local LLM server |
| Llama3 | Language model |
| nomic-embed-text | Embedding model |
| Docker | Containerization |
| Kubernetes (kind) | Local orchestration |
| pypdf | PDF text extraction |
| python-multipart | File upload handling |

---

## Roadmap

- [x] Local RAG pipeline
- [x] PDF ingestion
- [x] Vector search with ChromaDB
- [x] Private answers from documents
- [x] Dockerized
- [x] Deployed on Kubernetes
- [x] Multi-pod architecture
- [x] ChromaDB as StatefulSet with PVC
- [x] File upload endpoint
- [x] Automated model pulling via K8s Job
- [ ] RAGAS evaluation scoring
- [ ] Deploy to EKS
- [ ] File upload frontend
- [ ] S3 storage for raw documents
- [ ] LangGraph agents
- [ ] Voice input via Whisper
