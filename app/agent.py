from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from app.rag import ask_private_docs

llm = ChatOllama(
    model="llama3.1",
    base_url="http://ollama:11434",
    temperature=0
)

_last_sources = []

@tool
def search_documents(question: str) -> str:
    """
    Search private documents using the RAG pipeline.
    Use this tool when the user asks anything about uploaded documents,
    policies, strategies, rules, or internal knowledge.
    """
    global _last_sources

    result = ask_private_docs(question)
    _last_sources = result.get("sources", [])

    return result["answer"]

tools = [search_documents]

agent = create_react_agent(
    model=llm,
    tools=tools
)

def run_agent(question: str) -> dict:
    """
    Runs the LangGraph agent and returns:
    - final answer
    - sources from retrieval
    - whether the retrieval tool was actually called
    """
    global _last_sources
    _last_sources = []

    result = agent.invoke({
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a private document assistant. "
                    "Use the search_documents tool whenever the question could be answered "
                    "from uploaded private documents. Do not invent facts."
                )
            },
            {
                "role": "user",
                "content": question
            }
        ]
    })

    return {
        "answer": result["messages"][-1].content,
        "sources": _last_sources,
        "tool_was_called": len(_last_sources) > 0
    }
