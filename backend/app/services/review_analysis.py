from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.core.db import get_conn
from app.services.theme_classifier import bucket_phrases_by_theme, summarize_theme_counts


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "here", "him", "his",
    "i", "if", "in", "into", "is", "it", "its", "itself", "me", "my",
    "of", "on", "or", "our", "ours", "she", "so", "than", "that", "the",
    "their", "theirs", "them", "themselves", "there", "they", "this", "those",
    "to", "too", "us", "very", "was", "we", "were", "what", "when", "where",
    "which", "who", "whom", "why", "will", "with", "would", "you", "your",
    "yours", "about", "after", "again", "all", "also", "am", "any", "around",
    "because", "before", "between", "both", "can", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "further", "just",
    "more", "most", "much", "no", "nor", "not", "now", "off", "once", "only",
    "other", "out", "over", "own", "same", "should", "some", "such", "then",
    "through", "under", "until", "up", "while", "years", "year",
    "went", "got", "go", "going", "came", "come", "back", "even",

    # generic business/service words (intentionally minimal — avoid removing useful terms)
    "place", "service", "services", "work", "worked", "working", "job",
    "staff", "team", "business", "customer",

    # weak review filler
    "experience", "time", "times", "thing", "things", "way", "lot", "lots",
    "need", "needs", "needed", "say", "said", "make", "made", "well", "really",
    "every", "text", "texts", "these", "get", "done", "take", "always",

    # legacy brand/entity words (auto-shop specific, kept for backward compat)
    "prewitt", "meineke", "sumner", "sumners", "vb", "autoworks", "mt", "jake",
}

POSITIVE_HINTS = {
    "honest", "friendly", "professional", "fast", "quick", "affordable",
    "fair", "reasonable", "transparent", "helpful", "knowledgeable",
    "courteous", "great", "excellent", "amazing", "trustworthy", "reliable",
    "recommended", "recommend", "quality", "personable",
}

NEGATIVE_HINTS = {
    "expensive", "slow", "rude", "dishonest", "overpriced", "terrible",
    "bad", "awful", "poor", "unprofessional", "confusing", "frustrating",
    "late", "delay", "issue", "problem", "worst", "misleading",
    "scam", "broke", "broken",
}


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    text = normalize_text(text)
    tokens = text.split()
    return [
        t for t in tokens
        if len(t) >= 3
        and t not in STOPWORDS
        and not t.isdigit()
    ]


def get_reviews_for_business(business_id: str) -> List[Dict[str, Any]]:
    sql = """
    select
        competitor_id,
        rating,
        review_text
    from public.google_reviews
    where business_id = %s
      and review_text is not null
      and length(trim(review_text)) > 0
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id,))
            return cur.fetchall()


def get_reviews_for_competitor(business_id: str, competitor_id: str) -> List[Dict[str, Any]]:
    sql = """
    select
        competitor_id,
        rating,
        review_text
    from public.google_reviews
    where business_id = %s
      and competitor_id = %s
      and review_text is not null
      and length(trim(review_text)) > 0
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (business_id, competitor_id))
            return cur.fetchall()


def extract_top_terms_from_reviews(
    reviews: List[Dict[str, Any]],
    *,
    top_n: int = 15,
) -> List[Tuple[str, int]]:
    counter: Counter[str] = Counter()

    for row in reviews:
        text = row.get("review_text") or ""
        counter.update(tokenize(text))

    return counter.most_common(top_n)


def extract_phrases_by_sentiment(
    reviews: List[Dict[str, Any]],
    sentiment: Optional[str] = None,
    limit: int = 5,
) -> Any:
    """
    Deterministic phrase extraction from review text.

    Returns:
      - if sentiment is None:
          {
              "praise": [(phrase, count), ...],
              "complaint": [(phrase, count), ...]
          }
      - if sentiment == "praise":
          [phrase, phrase, ...]
      - if sentiment == "complaint":
          [phrase, phrase, ...]
    """
    praise_keywords = [
        # Trust / reliability
        "honest",
        "trustworthy",
        "reliable",
        "dependable",
        "genuine",
        "caring",
        # Speed / efficiency
        "fast",
        "quick",
        "prompt",
        "efficient",
        "timely",
        "same day",
        # Professionalism
        "professional",
        "friendly",
        "knowledgeable",
        "courteous",
        "welcoming",
        "compassionate",
        "attentive",
        "kind",
        "warm",
        "patient",
        "personable",
        "skilled",
        "experienced",
        "expert",
        # Dental/healthcare-specific praise
        "gentle",
        "painless",
        "comfortable",
        "thorough",
        "clean",
        "careful",
        "calming",
        "relaxing",
        "anxiety-free",
        "no pain",
        "pain free",
        "excellent dentist",
        "great dentist",
        "best dentist",
        # Communication
        "responsive",
        "helpful",
        "great communication",
        "kept me updated",
        "explained",
        "answered",
        # Pricing
        "fair price",
        "reasonable price",
        "affordable",
        "worth it",
        # Quality
        "excellent work",
        "great work",
        "quality",
        "outstanding",
        "perfect",
        "exceptional",
        # Convenience
        "easy",
        "convenient",
        "easy to schedule",
        "easy scheduling",
        "on time",
    ]

    complaint_keywords = [
        "expensive",
        "overpriced",
        "too expensive",
        "slow",
        "late",
        "rude",
        "unprofessional",
        "poor communication",
        "no response",
        "didn't respond",
        "hard to reach",
        "confusing",
        "bad work",
        "poor quality",
        "inconvenient",
        "difficult",
        "painful",
        "hurt",
        "rough",
        "dismissed",
        "rushed",
        "cancelled",
        "rescheduled",
        "long wait",
        "waiting too long",
        "billing issue",
        "insurance issue",
        "upselling",
        "unnecessary",
    ]

    praise_counter: Counter[str] = Counter()
    complaint_counter: Counter[str] = Counter()

    for row in reviews:
        text = str(row.get("review_text") or "").lower().strip()
        if not text:
            continue

        for phrase in praise_keywords:
            if phrase in text:
                praise_counter[phrase] += 1

        for phrase in complaint_keywords:
            if phrase in text:
                complaint_counter[phrase] += 1

    praise_list = [phrase for phrase, _ in praise_counter.most_common(limit)]
    complaint_list = [phrase for phrase, _ in complaint_counter.most_common(limit)]

    if sentiment == "praise":
        return praise_list

    if sentiment == "complaint":
        return complaint_list

    return {
        "praise": praise_counter.most_common(limit),
        "complaint": complaint_counter.most_common(limit),
    }


def summarize_competitor_review_signals(
    business_id: str,
    competitor_id: str,
) -> Dict[str, Any]:
    reviews = get_reviews_for_competitor(business_id, competitor_id)

    praise_phrases = extract_phrases_by_sentiment(reviews, "praise")
    complaint_phrases = extract_phrases_by_sentiment(reviews, "complaint")

    praise_theme_buckets = bucket_phrases_by_theme(praise_phrases)
    praise_theme_counts = summarize_theme_counts(praise_phrases)

    complaint_theme_buckets = bucket_phrases_by_theme(complaint_phrases)
    complaint_theme_counts = summarize_theme_counts(complaint_phrases)

    return {
        "review_count": len(reviews),
        "top_terms": extract_top_terms_from_reviews(reviews),
        "praise_phrases": praise_phrases,
        "complaint_phrases": complaint_phrases,
        "praise_theme_buckets": praise_theme_buckets,
        "praise_theme_counts": praise_theme_counts,
        "complaint_theme_buckets": complaint_theme_buckets,
        "complaint_theme_counts": complaint_theme_counts,
    }


def compare_owner_vs_competitor_terms(
    business_id: str,
    owner_competitor_id: str,
    competitor_id: str,
) -> Dict[str, Any]:
    owner_reviews = get_reviews_for_competitor(business_id, owner_competitor_id)
    competitor_reviews = get_reviews_for_competitor(business_id, competitor_id)

    owner_terms = dict(extract_top_terms_from_reviews(owner_reviews, top_n=50))
    competitor_terms = dict(extract_top_terms_from_reviews(competitor_reviews, top_n=50))

    owner_only: List[Tuple[str, int]] = []
    competitor_only: List[Tuple[str, int]] = []

    for term, count in owner_terms.items():
        if term not in competitor_terms:
            owner_only.append((term, count))

    for term, count in competitor_terms.items():
        if term not in owner_terms:
            competitor_only.append((term, count))

    owner_only.sort(key=lambda x: x[1], reverse=True)
    competitor_only.sort(key=lambda x: x[1], reverse=True)

    return {
        "owner_review_count": len(owner_reviews),
        "competitor_review_count": len(competitor_reviews),
        "owner_only_terms": owner_only[:10],
        "competitor_only_terms": competitor_only[:10],
    }


def _format_term_list(items: List[Any], limit: int = 3) -> List[str]:
    """
    Accept either:
    - [("term", count), ...]
    - ["term", "term", ...]
    and always return:
    - ["term", "term", ...]
    """
    if not items:
        return []

    output: List[str] = []

    for item in items[:limit]:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            output.append(str(item[0]))
        else:
            output.append(str(item))

    return [x for x in output if x]


def _join_terms_for_sentence(items: List[str]) -> str:
    items = [str(x).strip() for x in items if str(x).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def extract_themes_from_website_copy(website_text: str) -> Dict[str, Any]:
    """
    Placeholder deterministic website theme extraction.
    Uses the same classifier layer by scanning simple phrases/keywords from site copy.
    """
    if not website_text or not str(website_text).strip():
        empty_counts = {
            "trust": 0,
            "pricing": 0,
            "speed": 0,
            "professionalism": 0,
            "communication": 0,
            "quality": 0,
            "convenience": 0,
        }
        return {
            "website_phrases": [],
            "website_theme_buckets": {k: [] for k in empty_counts},
            "website_theme_counts": empty_counts,
        }

    text = normalize_text(website_text)
    tokens = tokenize(text)

    # Use tokens + a few multi-word phrase checks
    candidate_phrases: List[str] = list(tokens)

    multi_word_candidates = [
        "same day",
        "fair price",
        "reasonable price",
        "great communication",
        "kept me updated",
        "excellent work",
        "high quality",
        "easy scheduling",
        "on time",
    ]

    for phrase in multi_word_candidates:
        if phrase in text:
            candidate_phrases.append(phrase)

    theme_buckets = bucket_phrases_by_theme(candidate_phrases)
    theme_counts = summarize_theme_counts(candidate_phrases)

    return {
        "website_phrases": candidate_phrases,
        "website_theme_buckets": theme_buckets,
        "website_theme_counts": theme_counts,
    }


def build_messaging_mismatch_insight(
    *,
    business_id: str,
    competitor_id: str,
    competitor_name: str,
    website_text: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Compare review-language themes to placeholder website-language themes.
    """
    review_signals = summarize_competitor_review_signals(business_id, competitor_id)
    review_counts = review_signals.get("praise_theme_counts", {}) or {}

    website_signals = extract_themes_from_website_copy(website_text)
    website_counts = website_signals.get("website_theme_counts", {}) or {}

    all_themes = [
        "trust",
        "pricing",
        "speed",
        "professionalism",
        "communication",
        "quality",
        "convenience",
    ]

    review_led: List[str] = []
    website_led: List[str] = []
    missing_from_website: List[str] = []

    for theme in all_themes:
        review_value = int(review_counts.get(theme, 0) or 0)
        website_value = int(website_counts.get(theme, 0) or 0)

        if review_value > 0:
            review_led.append(theme)

        if website_value > 0:
            website_led.append(theme)

        if review_value > 0 and website_value == 0:
            missing_from_website.append(theme)

    if not review_led:
        return None

    # Only surface a meaningful gap — require at least 2 review themes missing from website,
    # and the top review theme must be absent. Single-theme differences are too noisy.
    top_review_theme = review_led[0] if review_led else None
    top_theme_missing = top_review_theme in missing_from_website if top_review_theme else False

    if len(missing_from_website) < 2 or not top_theme_missing:
        return None

    review_text = _join_terms_for_sentence(review_led[:3])
    website_text_summary = _join_terms_for_sentence(website_led[:3]) if website_led else "general service language"

    summary = (
        f"Customers repeatedly praise {competitor_name} for {review_text}, but the current "
        f"website messaging emphasizes {website_text_summary}."
    )

    return {
        "type": "messaging_mismatch",
        "severity": "info" if missing_from_website else "success",
        "summary": summary,
        "details": {
            "competitor_id": competitor_id,
            "competitor_name": competitor_name,
            "review_praise_phrases": review_signals.get("praise_phrases", []),
            "review_theme_buckets": review_signals.get("praise_theme_buckets", {}),
            "review_theme_counts": review_counts,
            "website_phrases": website_signals.get("website_phrases", []),
            "website_theme_buckets": website_signals.get("website_theme_buckets", {}),
            "website_theme_counts": website_counts,
            "missing_from_website": missing_from_website,
        },
    }

def _top_themes_from_counts(theme_counts: Dict[str, Any], limit: int = 3) -> List[str]:
    ordered = [
        "trust",
        "pricing",
        "speed",
        "professionalism",
        "communication",
        "quality",
        "convenience",
    ]

    ranked = []
    for theme in ordered:
        value = int(theme_counts.get(theme, 0) or 0)
        if value > 0:
            ranked.append((theme, value))

    ranked.sort(key=lambda x: (-x[1], ordered.index(x[0])))
    return [theme for theme, _ in ranked[:limit]]


def _theme_label(theme: str) -> str:
    labels = {
        "trust": "trust",
        "pricing": "pricing",
        "speed": "speed",
        "professionalism": "professionalism",
        "communication": "communication",
        "quality": "quality",
        "convenience": "convenience",
    }
    return labels.get(theme, theme)


def _theme_sentence_from_counts(theme_counts: Dict[str, Any], limit: int = 3) -> str:
    top = _top_themes_from_counts(theme_counts, limit=limit)
    labels = [_theme_label(t) for t in top]
    return _join_terms_for_sentence(labels)

def build_praise_themes_insight(
    business_id: str,
    competitor_id: str,
    competitor_name: str,
) -> Optional[Dict[str, Any]]:
    signals = summarize_competitor_review_signals(business_id, competitor_id)
    theme_counts = signals.get("praise_theme_counts", {}) or {}
    themes_text = _theme_sentence_from_counts(theme_counts, limit=3)

    if not themes_text:
        themes = _format_term_list(signals["praise_phrases"])
        themes_text = _join_terms_for_sentence(themes)

    if not themes_text:
        return None

    return {
        "type": "praise_themes",
        "severity": "info",
        "summary": f"Customers consistently praise {competitor_name} for {themes_text}.",
        "details": {
            "competitor_id": competitor_id,
            "competitor_name": competitor_name,
            "review_count": signals["review_count"],
            "praise_phrases": signals["praise_phrases"],
            "praise_theme_buckets": signals["praise_theme_buckets"],
            "praise_theme_counts": signals["praise_theme_counts"],
        },
    }


def build_complaint_themes_insight(
    business_id: str,
    competitor_id: str,
    competitor_name: str,
) -> Optional[Dict[str, Any]]:
    signals = summarize_competitor_review_signals(business_id, competitor_id)
    theme_counts = signals.get("complaint_theme_counts", {}) or {}
    complaints_text = _theme_sentence_from_counts(theme_counts, limit=3)

    if not complaints_text:
        complaints = _format_term_list(signals["complaint_phrases"])
        complaints_text = _join_terms_for_sentence(complaints)

    if not complaints_text:
        return None

    return {
        "type": "complaint_themes",
        "severity": "warning",
        "summary": f"Customers most often complain about {complaints_text} in reviews for {competitor_name}.",
        "details": {
            "competitor_id": competitor_id,
            "competitor_name": competitor_name,
            "review_count": signals["review_count"],
            "complaint_phrases": signals["complaint_phrases"],
            "complaint_theme_buckets": signals["complaint_theme_buckets"],
            "complaint_theme_counts": signals["complaint_theme_counts"],
        },
    }


def build_hidden_opportunity_insight(
    *,
    business_id: str,
    owner_competitor_id: str,
    owner_name: str,
    competitor_id: str,
    competitor_name: str,
) -> Optional[Dict[str, Any]]:
    owner_signals = summarize_competitor_review_signals(business_id, owner_competitor_id)
    competitor_signals = summarize_competitor_review_signals(business_id, competitor_id)

    owner_counts = owner_signals.get("praise_theme_counts", {}) or {}
    competitor_counts = competitor_signals.get("praise_theme_counts", {}) or {}

    all_themes = [
        "trust",
        "pricing",
        "speed",
        "professionalism",
        "communication",
        "quality",
        "convenience",
    ]

    owner_wins: List[str] = []
    competitor_wins: List[str] = []

    for theme in all_themes:
        owner_value = int(owner_counts.get(theme, 0) or 0)
        competitor_value = int(competitor_counts.get(theme, 0) or 0)

        if owner_value > competitor_value:
            owner_wins.append(theme)
        elif competitor_value > owner_value:
            competitor_wins.append(theme)

    owner_top = owner_wins[:3]
    competitor_top = competitor_wins[:3]

    if not owner_top and not competitor_top:
        return None

    owner_text = _join_terms_for_sentence(owner_top)
    competitor_text = _join_terms_for_sentence(competitor_top)

    if owner_top and competitor_top:
        summary = (
            f"You are winning on {owner_text}, while {competitor_name} is winning on "
            f"{competitor_text}. Reposition by emphasizing {owner_text} more clearly."
        )
    elif owner_top:
        summary = (
            f"You are winning on {owner_text}. Reposition by making that advantage more visible "
            f"in your messaging."
        )
    else:
        summary = (
            f"{competitor_name} is winning on {competitor_text}. Consider repositioning your "
            f"messaging to address that gap."
        )

    return {
        "type": "hidden_opportunity",
        "severity": "info",
        "summary": summary,
        "details": {
            "owner_competitor_id": owner_competitor_id,
            "owner_name": owner_name,
            "competitor_id": competitor_id,
            "competitor_name": competitor_name,
            "owner_praise_phrases": owner_signals.get("praise_phrases", []),
            "competitor_praise_phrases": competitor_signals.get("praise_phrases", []),
            "owner_praise_theme_buckets": owner_signals.get("praise_theme_buckets", {}),
            "owner_praise_theme_counts": owner_signals.get("praise_theme_counts", {}),
            "competitor_praise_theme_buckets": competitor_signals.get("praise_theme_buckets", {}),
            "competitor_praise_theme_counts": competitor_signals.get("praise_theme_counts", {}),
            "competitor_complaint_theme_buckets": competitor_signals.get("complaint_theme_buckets", {}),
            "competitor_complaint_theme_counts": competitor_signals.get("complaint_theme_counts", {}),
            "owner_winning_themes": owner_top,
            "competitor_winning_themes": competitor_top,
        },
    }