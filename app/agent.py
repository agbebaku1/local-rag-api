from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from app.rag import ask_private_docs

llm = ChatOllama(
    model="llama3.1",
    base_url="http://ollama:11434"
)

@tool
def search_documents(question: str) -> str:
    """Search private documents to answer questions about company policy."""
    result = ask_private_docs(question)
    return result["answer"]

tools = [search_documents]
agent = create_react_agent(llm, tools)

def run_agent(question: str) -> str:
    result = agent.invoke({
        "messages": [{"role": "user", "content": question}]
    })
    return result["messages"][-1].content
