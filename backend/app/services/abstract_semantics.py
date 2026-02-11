from __future__ import annotations

import re
from dataclasses import asdict, dataclass


ABSTRACT_LEXICON = {
    "none",
    "no",
    "nothing",
    "without",
    "not",
    "empty",
    "all",
    "any",
    "some",
    "every",
    "each",
    "more",
    "less",
    "same",
    "different",
    "other",
}

NEGATION_TOKENS = {"no", "not", "without", "none", "nothing"}
ABSTRACT_PARTS_OF_SPEECH = {
    "pronoun",
    "determiner",
    "preposition",
    "conjunction",
    "adverb",
    "quantifier",
}


@dataclass(slots=True)
class AbstractIntent:
    is_abstract: bool
    reason_codes: list[str]
    contrast_subject: str
    contrast_pattern: str = "single_frame_contrast"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", (value or "").lower())


def _extract_contrast_subject(context: str, category: str, fallback_word: str) -> str:
    text = (context or "").lower()
    match = re.search(r"(?:without|no|none|not)\s+([a-zA-Z][a-zA-Z\s-]{1,40})", text)
    if match:
        candidate = match.group(1).strip().split(" ")[0]
        if candidate:
            return candidate

    if category.strip():
        return category.strip()
    if fallback_word.strip():
        return fallback_word.strip()
    return "target object"


def detect_abstract_intent(
    *,
    word: str,
    part_of_sentence: str,
    context: str,
    category: str,
) -> AbstractIntent:
    reason_codes: list[str] = []
    normalized_word = (word or "").strip().lower()
    normalized_pos = (part_of_sentence or "").strip().lower()
    context_tokens = set(_tokenize(context))

    if normalized_word in ABSTRACT_LEXICON:
        reason_codes.append("lexicon_match")
    if any(token in context_tokens for token in NEGATION_TOKENS):
        reason_codes.append("context_negation")
    if normalized_pos in ABSTRACT_PARTS_OF_SPEECH:
        reason_codes.append("pos_abstract")
    if normalized_word.endswith("less"):
        reason_codes.append("suffix_less")

    is_abstract = len(reason_codes) > 0
    contrast_subject = _extract_contrast_subject(context=context, category=category, fallback_word=word)
    return AbstractIntent(
        is_abstract=is_abstract,
        reason_codes=reason_codes,
        contrast_subject=contrast_subject,
    )
