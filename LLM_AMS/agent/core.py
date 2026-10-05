"""Wire up LLM + prompts + AMSContext + compiled LangGraph."""

import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from agent.ams_engine.engine import AMSContext
from agent.prompts import get_prompts
from agent.workflows.workflow import create_workflow

load_dotenv()


def make_llm(provider: str = "ollama"):
    """Build the LLM client. Defaults to Ollama (matches pv-curve)."""
    if provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = ChatOpenAI(model=model, api_key=os.getenv("OPENAI_API_KEY"))
        llm._model_name = model
        return llm
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    llm = ChatOllama(model=model, base_url=base_url, temperature=0)
    llm._model_name = model
    return llm


def setup_dependencies(provider: str = "ollama"):
    """Return (llm, prompts, ams_ctx)."""
    return make_llm(provider), get_prompts(), AMSContext()


def create_graph(provider: str = "ollama"):
    llm, prompts, ams_ctx = setup_dependencies(provider)
    graph = create_workflow(llm, prompts, ams_ctx)
    return graph, llm, ams_ctx
