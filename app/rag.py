import os
import chromadb
from app.ollama_client import embed_text, ask_llama

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("RAG_CHROMA_PORT", "8001"))

client = chromadb.HttpClient(
    host=CHROMA_HOST,
    port=CHROMA_PORT
)

collection = client.get_or_create_collection(
    name="private_docs"
)

def chunk_text(text: str, chunk_size: int = 300) -> list:
    return [
        text[i:i + chunk_size]
        for i in range(0, len(text), chunk_size)
    ]

def ingest_document(file_path: str):
    if file_path.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        text = "\n".join(
            page.extract_text()
            for page in reader.pages
            if page.extract_text()
        )
    else:
        with open(file_path, "r") as f:
            text = f.read()

    chunks = chunk_text(text)

    for index, chunk in enumerate(chunks):
        embedding = embed_text(chunk)

        collection.add(
            ids=[f"{file_path}-{index}"],
            documents=[chunk],
            embeddings=[embedding],
            metadatas=[{"source": file_path}],
        )

    return {
        "chunks_added": len(chunks),
        "source": file_path
    }

def ask_private_docs(question: str) -> dict:
    question_embedding = embed_text(question)

    results = collection.query(
        query_embeddings=[question_embedding],
        n_results=3,
    )

    context_chunks = results["documents"][0]
    context = "\n\n".join(context_chunks)

    prompt = f"""
Use this context to answer the question.

Context:
{context}

Question:
{question}
"""

    answer = ask_llama(prompt)

    return {
        "answer": answer,
        "retrieved_chunks": context_chunks
    }
