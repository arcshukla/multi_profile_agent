"""
profile_rag.py
--------------
Profile-specific RAG — wraps SemanticRAGEngine with:
  - PROFILE_TOPICS: the 8-label taxonomy for professional profiles
  - SPLIT_PROMPT / INTENT_PROMPT: loaded from LLMPromptsService (editable via Admin UI)

One instance per profile, constructed with the profile's own ChromaDB path.
"""

from typing import Callable, Optional

from app.rag.semantic_rag_engine import SemanticRAGEngine
from app.core.constants import DEFAULT_PROFILE_TOPICS, CHROMA_COLLECTION_NAME
from app.core.logging_config import get_logger, get_profile_logger

logger = get_logger(__name__)

PROFILE_TOPICS = DEFAULT_PROFILE_TOPICS  # single source of truth in constants


def build_profile_rag(
    db_path:   str,
    slug:      str,
    on_tokens: Optional[Callable[[str, int, int, int], None]] = None,
) -> SemanticRAGEngine:
    """
    Factory: create a SemanticRAGEngine configured for professional profiles.

    Args:
        db_path:   Path to the profile's ChromaDB directory
        slug:      Profile slug (used for logging)
        on_tokens: Optional callback(operation, prompt, completion, total)
                   fired after each LLM call so callers can persist token usage.

    Returns:
        Configured SemanticRAGEngine instance
    """
    # Import here to avoid circular imports at module load time
    from app.services.llm_prompts_service import llm_prompts_service

    prompts = llm_prompts_service.get_prompts()

    plog = get_profile_logger(slug)
    plog.info("Building ProfileRAG for '%s' at %s", slug, db_path)
    return SemanticRAGEngine(
        topic_labels    = PROFILE_TOPICS,
        split_prompt    = prompts["split_prompt"]["content"],
        intent_prompt   = prompts["intent_prompt"]["content"],
        db_path         = db_path,
        collection_name = CHROMA_COLLECTION_NAME,
        on_tokens       = on_tokens,
        logger          = plog,
    )
