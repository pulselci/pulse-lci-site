from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

STOP_WORDS = {
    "the", "and", "for", "are", "with", "that", "this", "was", "were", "have",
    "had", "but", "not", "you", "your", "our", "they", "them", "from", "been",
    "very", "really", "just", "into", "onto", "about", "there", "their", "would",
    "could", "should", "will", "than", "then", "when", "what", "where", "which",
    "while", "after", "before", "over", "under", "again", "also", "only", "more",
    "most", "much", "many", "some", "such", "each", "same", "because", "service",
    "place", "shop", "business", "company", "theyre", "ive", "im",
}

def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def tokenize(text: str) -> List[str]:
    text = normalize_text(text)
    words = text.split()
    return [w for w in words if len(w) >= 3 and w not in STOP_WORDS and not w.isdigit()]

def split_reviews_by_sentiment(reviews: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    praise: List[Dict[str, Any]] = []
    complaints: List[Dict[str, Any]] = []

    for r in reviews:
        rating = r.get("rating")
        if rating is None:
            continue
        if rating >= 4:
            praise.append(r)
        else:
            complaints.append(r)

    return praise, complaints

def top_terms(reviews: Iterable[Dict[str, Any]], top_n: int = 15) -> List[Tuple[str, int]]:
    counter: Counter[str] = Counter()

    for r in reviews:
        for token in tokenize(r.get("text") or ""):
            counter[token] += 1

    return counter.most_common(top_n)

def phrase_hits(reviews: Iterable[Dict[str, Any]], phrases: List[str]) -> Dict[str, int]:
    counts = {p: 0 for p in phrases}

    for r in reviews:
        text = normalize_text(r.get("text") or "")
        for phrase in phrases:
            if normalize_text(phrase) in text:
                counts[phrase] += 1

    return counts

def total_reviews(reviews: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for _ in reviews)
