from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .review_text import split_reviews_by_sentiment, top_terms

Insight = Dict[str, Any]

def _pct(count: int, total: int) -> int:
    if total <= 0:
        return 0
    return round((count / total) * 100)

def _first_meaningful_term(terms: List[Tuple[str, int]], min_count: int = 3) -> Optional[Tuple[str, int]]:
    for term, count in terms:
        if count >= min_count:
            return term, count
    return None

def build_hidden_strength_insight(
    owner_praise_terms: List[Tuple[str, int]],
    competitor_praise_terms: List[Tuple[str, int]],
    competitor_praise_total: int,
) -> Optional[Insight]:
    owner_words = {w for w, _ in owner_praise_terms}

    for word, count in competitor_praise_terms:
        if count >= 3 and word not in owner_words:
            return {
                "type": "hidden_strength",
                "severity": "high",
                "summary": f"Customers mention '{word}' in {_pct(count, competitor_praise_total)}% of competitor praise reviews, but that theme is not showing up strongly in your review profile.",
                "action": f"Test featuring '{word}' in your homepage hero, Google Business description, and ad copy.",
                "details": {
                    "term": word,
                    "mentions": count,
                    "review_bucket": "competitor_praise",
                },
            }
    return None

def build_competitor_weakness_insight(
    competitor_complaint_terms: List[Tuple[str, int]],
    competitor_complaint_total: int,
) -> Optional[Insight]:
    top = _first_meaningful_term(competitor_complaint_terms, min_count=3)
    if not top:
        return None

    word, count = top
    return {
        "type": "competitor_weakness",
        "severity": "high",
        "summary": f"'{word}' appears in {_pct(count, competitor_complaint_total)}% of competitor complaint reviews.",
        "action": f"Position your business against '{word}' with a trust or speed-focused message in marketing and on your Google profile.",
        "details": {
            "term": word,
            "mentions": count,
            "review_bucket": "competitor_complaints",
        },
    }

def build_messaging_gap_insight(
    owner_praise_terms: List[Tuple[str, int]],
    competitor_praise_terms: List[Tuple[str, int]],
) -> Optional[Insight]:
    owner_words = {w for w, _ in owner_praise_terms}

    for word, count in competitor_praise_terms:
        if count >= 3 and word not in owner_words:
            return {
                "type": "messaging_gap",
                "severity": "medium",
                "summary": f"Customers repeatedly use the word '{word}' in this market, but it is not appearing strongly in your review language.",
                "action": f"Add '{word}' to headline, service-page copy, and review request prompts where appropriate.",
                "details": {
                    "term": word,
                    "mentions": count,
                },
            }
    return None

def build_top_praise_theme_insight(
    owner_praise_terms: List[Tuple[str, int]],
    owner_praise_total: int,
) -> Optional[Insight]:
    top = _first_meaningful_term(owner_praise_terms, min_count=3)
    if not top:
        return None

    word, count = top
    return {
        "type": "top_customer_praise_theme",
        "severity": "info",
        "summary": f"Your customers most often praise '{word}', showing up in about {_pct(count, owner_praise_total)}% of positive reviews.",
        "action": f"Lean harder into '{word}' in messaging and ask future reviewers about that experience specifically.",
        "details": {
            "term": word,
            "mentions": count,
        },
    }

def build_simple_positioning_recommendation(
    owner_praise_terms: List[Tuple[str, int]],
    competitor_complaint_terms: List[Tuple[str, int]],
) -> Optional[Insight]:
    owner_top = _first_meaningful_term(owner_praise_terms, min_count=3)
    comp_top = _first_meaningful_term(competitor_complaint_terms, min_count=3)

    if not owner_top and not comp_top:
        return None

    owner_word = owner_top[0] if owner_top else None
    comp_word = comp_top[0] if comp_top else None

    if owner_word and comp_word:
        summary = f"You appear strongest around '{owner_word}', while competitors are weakest around '{comp_word}'."
        action = f"Test positioning copy that pairs '{owner_word}' with a contrast against '{comp_word}'."
    elif owner_word:
        summary = f"Your strongest repeat praise theme is '{owner_word}'."
        action = f"Make '{owner_word}' more central in your homepage and Google Business messaging."
    else:
        summary = f"Competitor complaints cluster around '{comp_word}'."
        action = f"Use marketing copy that reassures buyers around '{comp_word}'."

    return {
        "type": "positioning_recommendation",
        "severity": "high",
        "summary": summary,
        "action": action,
        "details": {
            "owner_term": owner_word,
            "competitor_term": comp_word,
        },
    }

def build_money_insights(
    owner_reviews: List[Dict[str, Any]],
    competitor_reviews: List[Dict[str, Any]],
    owner_name: str | None = None,
) -> List[Insight]:
    owner_praise, owner_complaints = split_reviews_by_sentiment(owner_reviews)
    comp_praise, comp_complaints = split_reviews_by_sentiment(competitor_reviews)

    owner_praise_terms = top_terms(owner_praise, top_n=15)
    comp_praise_terms = top_terms(comp_praise, top_n=15)
    comp_complaint_terms = top_terms(comp_complaints, top_n=15)

    insights: List[Insight] = []

    candidates = [
        build_top_praise_theme_insight(owner_praise_terms, len(owner_praise)),
        build_competitor_weakness_insight(comp_complaint_terms, len(comp_complaints)),
        build_hidden_strength_insight(owner_praise_terms, comp_praise_terms, len(comp_praise)),
        build_messaging_gap_insight(owner_praise_terms, comp_praise_terms),
        build_simple_positioning_recommendation(owner_praise_terms, comp_complaint_terms),
    ]

    seen_types = set()
    for insight in candidates:
        if not insight:
            continue
        if insight["type"] in seen_types:
            continue
        seen_types.add(insight["type"])
        insights.append(insight)

    return insights
