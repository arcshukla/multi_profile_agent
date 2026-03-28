"""
llm_prompts_service.py
----------------------
Manages the two system-level LLM prompts used by the RAG engine:

  split_prompt  — instructs the LLM how to split a CV/document into topic sections
  intent_prompt — instructs the LLM how to classify a user query into topic labels

Storage: system/llm_prompts.json
  Falls back to built-in defaults when the file does not exist.
  Admins edit only the "content" text; the key names and {placeholder} variables
  must be preserved for the engine to work correctly.
"""

import json
from pathlib import Path
from typing import Optional

from app.core.config import SYSTEM_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_STORE = SYSTEM_DIR / "llm_prompts.json"

# ── Built-in defaults ─────────────────────────────────────────────────────────
# These are the source-of-truth defaults.  profile_rag.py reads from this
# service so defaults only need to live here.

_DEFAULTS: dict[str, dict] = {
    "split_prompt": {
        "name": "Document Split Prompt",
        "description": (
            "Instructs the LLM how to split an uploaded document into labelled topic sections. "
            "Must preserve {topic_labels}, {source_name}, and {text} placeholders."
        ),
        "content": """\
You are processing a professional profile document for a semantic search index.

Split the document into logical sections. For each section assign exactly one topic label
from this list: {topic_labels}

Rules:
- Split at natural content boundaries (don't mid-sentence split)
- Each section should be self-contained and answerable as a unit
- contact: name, title, email, phone, LinkedIn, location
- summary: executive summary, career overview, objective statement
- experience: all work history (can be one section or split by employer)
- education: all degrees, universities, years
- skills: technology stack, tools, languages, platforms, cloud, frameworks
- awards: patents, certifications, awards, publications, recognitions
- recommendations: testimonials or endorsements written by other people
- other: anything else

Return ONLY a JSON array wrapped in ```json code blocks. Each element: {{"topic": "<label>", "text": "<full section text>"}}
No markdown, no explanation, just the array.

Document ({source_name}):
{text}

JSON array:\
""",
    },
    "intent_prompt": {
        "name": "Intent Classification Prompt",
        "description": (
            "Instructs the LLM how to classify a user question into 1-3 topic labels. "
            "Must preserve {topic_labels} and {query} placeholders."
        ),
        "content": """\
You classify user questions about a professional profile into topic categories.

Available topics: {topic_labels}

Return ONLY a JSON array wrapped in ```json code blocks of 1-3 topic labels that best match what the user is asking.
Examples:
- "how do I contact her" → ["contact"]
- "what is her education" → ["education"]
- "which companies did she work at" → ["experience"]
- "what are her technical skills" → ["skills"]
- "tell me about her background" → ["summary", "experience"]
- "what did her colleagues say" → ["recommendations"]
- "any patents or awards" → ["awards"]
- "tell me about her" → ["summary", "experience", "skills"]

User question: {query}

JSON array:\
""",
    },
}


class LLMPromptsService:
    """
    Load and save system LLM prompts.

    Admins may change the text of each prompt via the Admin UI.
    The placeholder variables ({topic_labels}, {text}, {query}, {source_name})
    must remain intact for the RAG engine to function.
    """

    def get_prompts(self) -> dict[str, dict]:
        """
        Return the current prompts dict.
        Falls back to built-in defaults if the store file does not exist or is corrupt.
        """
        if not _STORE.exists():
            return _copy(_DEFAULTS)

        try:
            raw = json.loads(_STORE.read_text(encoding="utf-8"))
            # Merge: ensure all default keys are present (handles future new keys)
            merged = _copy(_DEFAULTS)
            for key, val in raw.items():
                if key in merged and isinstance(val, dict) and "content" in val:
                    merged[key]["content"] = val["content"]
            return merged
        except Exception as e:
            logger.warning("Failed to read llm_prompts.json — using defaults: %s", e)
            return _copy(_DEFAULTS)

    def update_prompt(self, key: str, content: str) -> bool:
        """
        Update the text content of one prompt and persist.

        Returns True on success, False if key is unknown.
        """
        if key not in _DEFAULTS:
            logger.warning("Unknown LLM prompt key: '%s'", key)
            return False

        prompts = self.get_prompts()
        prompts[key]["content"] = content
        return self._save(prompts)

    def restore_defaults(self) -> bool:
        """Delete the store file so built-in defaults are used."""
        try:
            if _STORE.exists():
                _STORE.unlink()
                logger.info("LLM prompts restored to defaults")
            return True
        except Exception as e:
            logger.error("Failed to restore LLM prompt defaults: %s", e)
            return False

    def _save(self, prompts: dict) -> bool:
        try:
            # Persist only the content — name/description come from defaults
            payload = {k: {"content": v["content"]} for k, v in prompts.items()}
            _STORE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("LLM prompts saved to %s", _STORE)
            return True
        except Exception as e:
            logger.error("Failed to save LLM prompts: %s", e)
            return False


def _copy(d: dict) -> dict:
    """Deep-copy a two-level dict."""
    return {k: dict(v) for k, v in d.items()}


# Singleton
llm_prompts_service = LLMPromptsService()
