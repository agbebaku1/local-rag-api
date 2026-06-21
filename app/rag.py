import os
import chromadb
from app.ollama_client import embed_text, ask_llama
import hashlib

CHROMA_HOST = os.getenv("CHROMA_HOST", "chroma")
CHROMA_PORT = 8000

client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)

collection = client.get_or_create_collection(
    name="private_docs",
    metadata={"hnsw:space": "cosine"}
)

def chunk_text(text: str, chunk_size: int = 300, separators: list = None) -> list:
    if separators is None:
        separators = ["\n\n", "\n", ". ", " ", ""]

    separator = separators[0]
    remaining_separators = separators[1:]

    if separator == "":
        pieces = list(text)
    else:
        pieces = text.split(separator)

    chunks = []
    current_chunk = ""

    for piece in pieces:
        candidate = current_chunk + (separator if current_chunk else "") + piece

        if len(candidate) <= chunk_size:
            current_chunk = candidate
        else:
            if current_chunk:
                chunks.append(current_chunk)

            if len(piece) > chunk_size and remaining_separators:
                chunks.extend(chunk_text(piece, chunk_size, remaining_separators))
                current_chunk = ""
            else:
                current_chunk = piece

    if current_chunk:
        chunks.append(current_chunk)

    return chunks



def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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
    doc_hash = content_hash(text)

    existing = collection.get(where={"content_hash": doc_hash}, limit=1)
    if existing.get("ids"):
        return {
            "chunks_added": 0,
            "source": file_path,
            "document_name": os.path.basename(file_path),
            "status": "skipped_duplicate"
        }

    chunks = chunk_text(text)

    for index, chunk in enumerate(chunks):
        embedding = embed_text(chunk)

        collection.add(
            ids=[f"{file_path}-{index}"],
            documents=[chunk],
            embeddings=[embedding],
            metadatas=[{
                "source": file_path,
                "content_hash": doc_hash,
                "document_name": os.path.basename(file_path),
                "chunk_index": index
            }],
        )

    return {
        "chunks_added": len(chunks),
        "source": file_path,
        "document_name": os.path.basename(file_path)
    }

def _query_chroma(question: str, document_name: str = None):
    question_embedding = embed_text(question)

    if document_name:
        return collection.query(
            query_embeddings=[question_embedding],
            n_results=3,
            where={"document_name": document_name}
        )

    return collection.query(
        query_embeddings=[question_embedding],
        n_results=3,
    )

def _build_sources(context_chunks, metadatas, distances):
    sources = []

    for chunk, meta, dist in zip(context_chunks, metadatas, distances):
        sources.append({
            "source": meta.get("source"),
            "document_name": meta.get("document_name"),
            "chunk_index": meta.get("chunk_index"),
            "relevance_score": round(1 - dist, 3),
            "chunk": chunk
        })

    return sources

def ask_private_docs(question: str, document_name: str = None) -> dict:
    results = _query_chroma(question, document_name)

    context_chunks = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    context = "\n\n".join(context_chunks)

    prompt = f"""Use this context to answer the question.

Context:
{context}

Question:
{question}

Rules:
- Answer only from the context.
- Do not add outside knowledge.
- If the answer is not in the context, say you don't know.
"""

    answer = ask_llama(prompt)
    sources = _build_sources(context_chunks, metadatas, distances)

    return {
        "answer": answer,
        "retrieved_chunks": context_chunks,
        "sources": sources
    }

def debug_search(question: str, document_name: str = None) -> dict:
    results = _query_chroma(question, document_name)

    context_chunks = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    return {
        "question": question,
        "document_name": document_name,
        "results": _build_sources(context_chunks, metadatas, distances)
    }
def get_unique_documents() -> list:
    results = collection.get(include=["metadatas"])
    metadatas = results.get("metadatas", [])

    unique_names = set()

    for meta in metadatas:
        if meta and meta.get("document_name"):
            unique_names.add(meta["document_name"])

    return sorted(list(unique_names))