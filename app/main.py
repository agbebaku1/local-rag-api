from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from app.rag import ingest_document, ask_private_docs, debug_search, get_unique_documents
from app.agent import run_agent
import os


app = FastAPI()

class AskRequest(BaseModel):
    question: str
    document_name: str | None = None

class IngestRequest(BaseModel):
    file_path: str = "app/data/policy.txt"

class AgentRequest(BaseModel):
    question: str

@app.get("/")
def health_check():
    return {"status": "running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

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
        "document_name": result["document_name"],
        "chunks_added": result["chunks_added"],
        "status": result.get("status", "ingested")
    }

@app.post("/ask")
def ask(request: AskRequest):
    result = ask_private_docs(
        question=request.question,
        document_name=request.document_name
    )

    return {
        "question": request.question,
        "document_name": request.document_name,
        "route": "local_ollama_private_rag",
        "answer": result["answer"],
        "sources": result["sources"],
        "retrieved_chunks": result["retrieved_chunks"]
    }

@app.post("/agent")
def agent_ask(request: AgentRequest):
    result = run_agent(request.question)

    return {
        "question": request.question,
        "route": "langgraph_agent",
        "answer": result["answer"],
        "sources": result["sources"],
        "tool_was_called": result["tool_was_called"]
    }

@app.post("/debug-search")
def debug_search_endpoint(request: AskRequest):
    return debug_search(
        question=request.question,
        document_name=request.document_name
    )
@app.get("/documents")
def list_documents_endpoint():
    try:
        return {"documents": get_unique_documents()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))