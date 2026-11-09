# translation.py — chunked async translation
import asyncio
import logging
from typing import List, Optional, Tuple

from deep_translator import GoogleTranslator

from config import TRANSLATE_MAX_CHUNK, TRANSLATE_TARGET

logger = logging.getLogger(__name__)

# Reuse one translator for the target language; GoogleTranslator is stateless per call.
_translator = GoogleTranslator(source="auto", target=TRANSLATE_TARGET)


def _split_text(text: str, limit: int = TRANSLATE_MAX_CHUNK) -> List[str]:
    """Split at paragraph > newline > space, never mid-word unless forced."""
    if len(text) <= limit:
        return [text]
    chunks, remaining = [], text
    while len(remaining) > limit:
        window = remaining[:limit]
        for sep in ("\n\n", "\n", " "):
            pos = window.rfind(sep)
            if pos > limit * 0.6:
                cut = pos + len(sep)
                break
        else:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _translate_sync(text: str) -> str:
    return _translator.translate(text)


async def translate_to_english(text: str) -> Tuple[Optional[str], str]:
    """
    Returns (translated_text, source_lang_hint).
    Never raises; returns (None, "error") on failure.
    """
    if not text or not text.strip():
        return None, "empty"

    chunks = _split_text(text)
    translated_parts: List[str] = []

    for chunk in chunks:
        try:
            result = await asyncio.to_thread(_translate_sync, chunk)
            if result is None:
                logger.warning("Translator returned None for a chunk")
                return None, "error"
            translated_parts.append(result)
        except Exception as e:
            logger.exception("Translation failed for chunk: %s", e)
            return None, "error"

    # GoogleTranslator with source="auto" does not return the detected code;
    # the source hint is left as "auto" and the UI does not depend on it.
    return "\n".join(translated_parts), "auto"
