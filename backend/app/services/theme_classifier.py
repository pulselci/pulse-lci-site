# app/services/theme_classifier.py

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


THEMES = {
    "trust",
    "pricing",
    "speed",
    "professionalism",
    "communication",
    "quality",
    "convenience",
}


# Deterministic keyword bank.
# Keep this tight and practical for shipping.
THEME_KEYWORDS: Dict[str, List[str]] = {
    "trust": [
        "honest",
        "trust",
        "trustworthy",
        "reliable",
        "dependable",
        "fair",
        "transparent",
        "integrity",
        "genuine",
        "upfront",
        "sincere",
        "credible",
    ],
    "pricing": [
        "price",
        "priced",
        "pricing",
        "cost",
        "value",
        "affordable",
        "expensive",
        "cheap",
        "reasonable",
        "fair price",
        "worth",
        "overpriced",
        "deal",
    ],
    "speed": [
        "fast",
        "quick",
        "prompt",
        "promptly",
        "timely",
        "rapid",
        "same day",
        "on time",
        "immediately",
        "efficient",
        "speedy",
    ],
    "professionalism": [
        "professional",
        "professionalism",
        "courteous",
        "respectful",
        "knowledgeable",
        "expert",
        "experienced",
        "polite",
        "friendly",
        "organized",
        "competent",
        "welcoming",
        "compassionate",
        "attentive",
        "kind",
        "warm",
        "patient",
        "personable",
        "skilled",
        "caring",
        "gentle",
    ],
    "communication": [
        "communication",
        "communicate",
        "communicative",
        "responsive",
        "response",
        "responded",
        "kept me updated",
        "explained",
        "explanation",
        "clear",
        "helpful",
        "answered",
        "called",
        "texted",
        "informed",
        "walked me through",
        "took time to explain",
    ],
    "quality": [
        "quality",
        "excellent work",
        "great work",
        "workmanship",
        "attention to detail",
        "fixed",
        "done right",
        "thorough",
        "clean",
        "perfect",
        "outstanding",
        "high quality",
        "exceptional",
        "painless",
        "comfortable",
        "careful",
        "precision",
        "best dentist",
        "great dentist",
        "excellent dentist",
    ],
    "convenience": [
        "convenient",
        "easy",
        "smooth",
        "simple",
        "hassle free",
        "pickup",
        "drop off",
        "location",
        "available",
        "availability",
        "scheduling",
        "schedule",
        "appointment",
        "nearby",
    ],
}


# Optional phrase cleanup before classification.
# These help convert messy fragments into more classifiable terms.
PHRASE_NORMALIZATION_MAP: Dict[str, str] = {
    "find honest": "honest",
    "super nice": "friendly",
    "good people": "friendly",
    "on-time": "on time",
    "same-day": "same day",
    "well priced": "reasonable price",
    "great price": "fair price",
    "good price": "fair price",
    "paper put": "",   # obvious junk fragment example
    "little paper put": "",
    "little": "",
    "put": "",
}


# Generic noise / low-signal terms that should not drive themes
STOPWORDS = {
    "the", "and", "for", "with", "they", "them", "was", "were", "very",
    "really", "just", "good", "great", "nice", "little", "much", "lot",
    "thing", "stuff", "place", "service", "company", "business", "people",
    "work", "job", "did", "done", "got", "get", "make", "made", "put",
}


@dataclass(frozen=True)
class ThemeMatch:
    raw_text: str
    normalized_text: str
    theme: Optional[str]
    confidence: float
    matched_keywords: List[str]


def _basic_normalize(text: str) -> str:
    """
    Lowercase, strip punctuation, collapse whitespace.
    """
    text = (text or "").strip().lower()
    text = text.replace("/", " ")
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _apply_phrase_normalization(text: str) -> str:
    """
    Apply deterministic phrase replacements.
    Longest phrases first to avoid partial collisions.
    """
    normalized = text

    for source, target in sorted(
        PHRASE_NORMALIZATION_MAP.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    ):
        pattern = r"\b" + re.escape(source) + r"\b"
        normalized = re.sub(pattern, target, normalized)

    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _tokenize(text: str) -> List[str]:
    return [t for t in text.split() if t and t not in STOPWORDS]


def _keyword_score(text: str, theme_keywords: List[str]) -> Tuple[int, List[str]]:
    """
    Deterministic score:
    - exact multi-word phrase match = +3
    - exact single-word token match = +2
    - substring fallback in normalized text = +1
    """
    tokens = set(_tokenize(text))
    matched: List[str] = []
    score = 0

    for kw in theme_keywords:
        kw_norm = _basic_normalize(kw)
        if not kw_norm:
            continue

        if " " in kw_norm:
            if re.search(r"\b" + re.escape(kw_norm) + r"\b", text):
                score += 3
                matched.append(kw)
        else:
            if kw_norm in tokens:
                score += 2
                matched.append(kw)
            elif kw_norm in text:
                score += 1
                matched.append(kw)

    return score, sorted(set(matched))


def classify_theme(raw_text: str) -> ThemeMatch:
    """
    Deterministically map a raw phrase/snippet to one theme.
    """
    original = raw_text or ""
    cleaned = _basic_normalize(original)
    normalized = _apply_phrase_normalization(cleaned)

    if not normalized:
        return ThemeMatch(
            raw_text=original,
            normalized_text="",
            theme=None,
            confidence=0.0,
            matched_keywords=[],
        )

    theme_scores: Dict[str, int] = {}
    theme_matches: Dict[str, List[str]] = {}

    for theme, keywords in THEME_KEYWORDS.items():
        score, matched = _keyword_score(normalized, keywords)
        theme_scores[theme] = score
        theme_matches[theme] = matched

    best_theme = None
    best_score = 0

    # Stable tie-break order for determinism
    ordered_themes = [
        "trust",
        "pricing",
        "speed",
        "professionalism",
        "communication",
        "quality",
        "convenience",
    ]

    for theme in ordered_themes:
        score = theme_scores.get(theme, 0)
        if score > best_score:
            best_score = score
            best_theme = theme

    if best_score <= 0:
        return ThemeMatch(
            raw_text=original,
            normalized_text=normalized,
            theme=None,
            confidence=0.0,
            matched_keywords=[],
        )

    # Lightweight deterministic confidence
    # 2 = weak, 3-4 = medium, 5+ = strong
    if best_score >= 5:
        confidence = 0.95
    elif best_score >= 3:
        confidence = 0.80
    else:
        confidence = 0.60

    return ThemeMatch(
        raw_text=original,
        normalized_text=normalized,
        theme=best_theme,
        confidence=confidence,
        matched_keywords=theme_matches.get(best_theme, []),
    )


def classify_many(raw_phrases: List[str]) -> List[ThemeMatch]:
    return [classify_theme(p) for p in raw_phrases]


def bucket_phrases_by_theme(raw_phrases: List[str]) -> Dict[str, List[Dict[str, object]]]:
    """
    Return phrases grouped by theme in a report-friendly structure.
    Unmatched phrases are excluded.
    """
    buckets: Dict[str, List[Dict[str, object]]] = {
        "trust": [],
        "pricing": [],
        "speed": [],
        "professionalism": [],
        "communication": [],
        "quality": [],
        "convenience": [],
    }

    for phrase in raw_phrases:
        result = classify_theme(phrase)
        if not result.theme:
            continue

        buckets[result.theme].append(
            {
                "raw_text": result.raw_text,
                "normalized_text": result.normalized_text,
                "confidence": result.confidence,
                "matched_keywords": result.matched_keywords,
            }
        )

    return buckets


def summarize_theme_counts(raw_phrases: List[str]) -> Dict[str, int]:
    counts = {theme: 0 for theme in sorted(THEMES)}
    for phrase in raw_phrases:
        result = classify_theme(phrase)
        if result.theme:
            counts[result.theme] += 1
    return counts