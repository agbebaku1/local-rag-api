import requests

OLLAMA_URL = "http://ollama:11434/api"

def embed_text(text: str) -> list:
    response = requests.post(
        f"{OLLAMA_URL}/embeddings",
        json={"model": "nomic-embed-text", "prompt": text},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["embedding"]

def ask_llama(prompt: str) -> str:
    response = requests.post(
        f"{OLLAMA_URL}/chat",
        json={
            "model": "llama3",
            "stream": False,
            "messages": [
                {"role": "system", "content": "Answer only from the provided context. If the answer is not in the context, say you don't know."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]
