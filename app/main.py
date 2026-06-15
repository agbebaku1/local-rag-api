from fastapi import FastAPI, UploadFile, File
from pydantic import BaseModel
from app.rag import ingest_document, ask_private_docs
import os

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

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    temp_path = f"/tmp/{file.filename}"
    contents = await file.read()
    with open(temp_path, "wb") as f:
        f.write(contents)
    result = ingest_document(temp_path)
    os.remove(temp_path)
    return {
        "filename": file.filename,
        "chunks_added": result["chunks_added"],
        "status": "ingested"
    }

@app.post("/ask")
def ask(request: AskRequest):
    result = ask_private_docs(request.question)
    return {
        "question": request.question,
        "route": "local_ollama_private_rag",
        "answer": result["answer"],
        "retrieved_chunks": result["retrieved_chunks"]
    }

from app.agent import run_agent

class AgentRequest(BaseModel):
    question: str

@app.post("/agent")
def agent_ask(request: AgentRequest):
    answer = run_agent(request.question)
    return {
        "question": request.question,
        "route": "langgraph_agent",
        "answer": answer
    }
