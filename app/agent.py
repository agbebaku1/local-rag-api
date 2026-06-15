from langgraph.prebuilt import create_react_agent
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from app.rag import ask_private_docs

@tool
def search_documents(question: str) -> str:
    """Search private documents to answer questions about company policies, contracts, or uploaded files."""
    result = ask_private_docs(question)
    return result["answer"]

llm = ChatOllama(model="llama3.1", base_url="http://localhost:11434")

agent = create_react_agent(
    model=llm,
    tools=[search_documents],
)

def run_agent(question: str) -> str:
    result = agent.invoke({
        "messages": [{"role": "user", "content": question}]
    })
    return result["messages"][-1].content
