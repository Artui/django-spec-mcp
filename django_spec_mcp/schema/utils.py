from __future__ import annotations

_SENTENCE_ENDINGS: tuple[str, ...] = (".", "!", "?")


def end_sentence(text: str) -> str:
    """``text`` with a full stop added, unless it already ends a sentence."""
    stripped = text.rstrip()
    return stripped if stripped.endswith(_SENTENCE_ENDINGS) else f"{stripped}."


__all__ = [
    "end_sentence",
]
