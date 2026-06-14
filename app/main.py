from fastapi import FastAPI
from pydantic import BaseModel
from app.rag import ingest_document, ask_private_docs

app = FastAPI()

class AskRequest(BaseModel):
    question: str

class IngestRequest(BaseModel):
    file_path: str = "app/data/policy.txt"

@app.get("/")
def health_check():
    return {"status": "running"}

@app.post("/ingest")
def ingest(request: IngestRequest):
    return ingest_document(request.file_path)

@app.post("/ask")
def ask(request: AskRequest):
    result = ask_private_docs(request.question)
    return {
        "question": request.question,
        "route": "local_ollama_private_rag",
        "answer": result["answer"],
        "retrieved_chunks": result["retrieved_chunks"]
    }
