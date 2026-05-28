from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

from .config import SETTINGS


def get_local_llm(model: str | None = None, temperature: float = 0.0) -> BaseChatModel:
    chosen_model = model or SETTINGS.ollama_model
    return ChatOllama(
        model=chosen_model,
        base_url=SETTINGS.ollama_base_url,
        temperature=temperature,
    )


def get_reasoning_llm() -> BaseChatModel:
    return get_local_llm(temperature=0.0)


def get_fallback_llm() -> BaseChatModel:
    return get_local_llm(model=SETTINGS.ollama_fallback_model, temperature=0.0)
