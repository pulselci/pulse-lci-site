from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


def format_insights_for_report(
    insights: List[Dict[str, Any]],
    *,
    owner_name: Optional[str] = None,
) -> str:
    """
    Converts review insight objects into cleaner client-facing narrative text.

    Premium version:
    - avoids repetitive competitor-by-competitor paragraphs
    - summarizes the market story first
    - calls out only the strongest competitor examples
    """

    grouped: dict[str, List[Dict[str, Any]]] = {}
    owner_name_norm = (owner_name or "").strip().lower()

    for insight in insights:
        details = insight.get("details") or {}
        name = str(details.get("competitor_name") or "Competitor").strip()
        name_norm = name.lower()

        owner_competitor_id = details.get("owner_competitor_id")
        competitor_id = details.get("competitor_id")

        if owner_competitor_id and competitor_id and str(owner_competitor_id) == str(competitor_id):
            continue

        if owner_name_norm and name_norm == owner_name_norm:
            continue

        if name_norm.endswith(" self"):
            continue

        grouped.setdefault(name, []).append(insight)

    def _clean(text: Any) -> str:
        return " ".join(str(text or "").split()).strip().rstrip(" .,")

    def _ensure_period(text: str) -> str:
        text = _clean(text)
        if not text:
            return ""
        if not text.endswith("."):
            text += "."
        return text

    def _split_themes(text: str) -> list[str]:
        text = _clean(text).lower()
        if not text:
            return []

        text = text.replace(" and ", ", ")
        parts = [p.strip() for p in text.split(",") if p.strip()]
        return parts

    competitor_rows: list[dict[str, Any]] = []
    owner_theme_counter: Counter[str] = Counter()
    competitor_theme_counter: Counter[str] = Counter()
    praise_theme_counter: Counter[str] = Counter()
    messaging_gap_rows: list[dict[str, str]] = []

    for competitor_name, items in grouped.items():
        praise: Optional[str] = None
        comp_strength: Optional[str] = None
        owner_strength: Optional[str] = None
        opportunity: Optional[str] = None
        gap_left: Optional[str] = None
        gap_right: Optional[str] = None

        raw_praise_phrases: list[str] = []  # actual words from review text
        review_count: int = 0

        for item in items:
            t = str(item.get("type") or "").strip()
            s = _clean(item.get("summary"))
            details = item.get("details") or {}

            if not s:
                continue

            if t == "praise_themes":
                praise = _clean(s.split(" for ", 1)[-1] if " for " in s else s)
                # Extract the actual matched vocabulary from reviews
                raw_phrases = details.get("praise_phrases") or []
                raw_praise_phrases = [str(p).strip() for p in raw_phrases if p][:6]
                # Use real Google total (market size), fall back to ingested count
                review_count = int(details.get("reviews_total") or details.get("review_count") or 0)

            elif t == "hidden_opportunity":
                if "while" in s and "Reposition by" in s:
                    try:
                        part1, rest = s.split("while", 1)
                        part2, reposition = rest.split("Reposition by", 1)

                        owner_strength = _clean(
                            part1
                            .replace("You are winning on", "")
                            .replace("you are winning on", "")
                            .replace("Positioning opening:", "")
                            .replace("you are outperforming on", "")
                            .replace("You are outperforming on", "")
                        )

                        comp_strength = _clean(
                            part2
                            .replace(f"{competitor_name} is winning on", "")
                            .replace(f"{competitor_name} wins on", "")
                            .replace("is winning on", "")
                            .replace("wins on", "")
                        )

                        opportunity = _clean(reposition)
                    except Exception:
                        pass

                elif "Reposition by" in s:
                    opportunity = _clean(s.split("Reposition by", 1)[-1])

            elif t == "messaging_mismatch":
                if "aligned" in s.lower():
                    continue

                if "but the current website messaging emphasizes" in s:
                    left, right = s.split("but the current website messaging emphasizes", 1)

                    left = left.replace("Customers repeatedly praise", "")
                    left = left.replace(competitor_name, "")
                    left = _clean(left).lower()

                    if left.startswith("for "):
                        left = left.replace("for ", "", 1)

                    right = right.split("Opportunity:", 1)[0]
                    right = _clean(right).lower()

                    gap_left = left
                    gap_right = right

        for theme in _split_themes(owner_strength or ""):
            owner_theme_counter[theme] += 1

        for theme in _split_themes(comp_strength or ""):
            competitor_theme_counter[theme] += 1

        for theme in _split_themes(praise or ""):
            praise_theme_counter[theme] += 1

        if gap_left and gap_right:
            messaging_gap_rows.append(
                {
                    "competitor_name": competitor_name,
                    "gap_left": gap_left,
                    "gap_right": gap_right,
                }
            )

        competitor_rows.append(
            {
                "competitor_name": competitor_name,
                "praise": praise,
                "raw_praise_phrases": raw_praise_phrases,
                "review_count": review_count,
                "comp_strength": comp_strength,
                "owner_strength": owner_strength,
                "opportunity": opportunity,
                "gap_left": gap_left,
                "gap_right": gap_right,
            }
        )

    if not competitor_rows:
        return ""

    owner_top = [theme for theme, _ in owner_theme_counter.most_common(3)]
    competitor_top = [theme for theme, _ in competitor_theme_counter.most_common(3)]
    praise_top = [theme for theme, _ in praise_theme_counter.most_common(3)]

    def _human_join(values: list[str]) -> str:
        vals = [v for v in values if v]
        if not vals:
            return ""
        if len(vals) == 1:
            return vals[0]
        if len(vals) == 2:
            return f"{vals[0]} and {vals[1]}"
        return f"{', '.join(vals[:-1])}, and {vals[-1]}"

    owner_phrase = _human_join(owner_top)
    competitor_phrase = _human_join(competitor_top)
    praise_phrase = _human_join(praise_top)

    sections: list[str] = []

    # Collect all raw praise phrases across all competitors for the market summary
    all_raw_phrases: Counter[str] = Counter()
    for row in competitor_rows:
        for phrase in row.get("raw_praise_phrases") or []:
            all_raw_phrases[phrase] += 1

    # Pick the most evocative actual words (avoid theme labels like "professionalism")
    _theme_label_words = {"professionalism", "quality", "speed", "trust", "pricing",
                          "communication", "convenience"}
    top_raw_phrases = [
        w for w, _ in all_raw_phrases.most_common(8)
        if w.lower() not in _theme_label_words
    ][:4]
    raw_phrase_str = _human_join(top_raw_phrases)

    # Fallback to theme phrase if no concrete words
    display_praise = raw_phrase_str or praise_phrase

    # ── Section 1: What patients are saying ──────────────────────────────
    what_patients_say: list[str] = []

    if display_praise:
        what_patients_say.append(
            _ensure_period(
                f"When patients in your market leave positive reviews, the words that come up most are: {display_praise}. "
                f"These are the signals that build trust before someone even picks up the phone"
            )
        )

    if what_patients_say:
        sections.append(
            "What Patients Are Saying\n"
            + "\n".join([s.strip() for s in what_patients_say])
        )

    # ── Section 2: Closest competitor = highest review count (biggest threat) ──
    # Sort by review count descending so the most-reviewed competitor comes first
    ranked_rows = sorted(
        competitor_rows,
        key=lambda r: r.get("review_count") or 0,
        reverse=True,
    )

    top_competitor = ranked_rows[0] if ranked_rows else None
    if top_competitor:
        top_name = top_competitor["competitor_name"]
        raw_phrases = top_competitor.get("raw_praise_phrases") or []
        actual_words = [w for w in raw_phrases if w.lower() not in _theme_label_words][:3]
        actual_word_str = _human_join(actual_words)

        comp_lines: list[str] = []
        if actual_word_str:
            comp_lines.append(
                _ensure_period(
                    f"Their patients use words like \"{actual_word_str}\" — that's the reputation they've built, "
                    f"and it's what new patients see when they're comparing options"
                )
            )
        elif top_competitor.get("praise"):
            comp_lines.append(
                _ensure_period(
                    f"Their patients are consistently praising them for {top_competitor['praise']}"
                )
            )

        if comp_lines:
            sections.append(
                f"Your Closest Competitor: {top_name}\n"
                + "\n".join([s.strip() for s in comp_lines])
            )

    # ── Section 3: Weak spot — prefer the same competitor, else best available ──
    if messaging_gap_rows:
        # Try to match the gap to the top competitor first
        top_name_lower = (top_competitor["competitor_name"] if top_competitor else "").lower()
        matched_gap = next(
            (g for g in messaging_gap_rows if g["competitor_name"].lower() == top_name_lower),
            messaging_gap_rows[0],
        )
        gap_name = matched_gap["competitor_name"]
        sections.append(
            f"{gap_name}'s Weak Spot\n"
            + _ensure_period(
                f"Their patients rave about {matched_gap['gap_left']}, "
                f"but their website leads with {matched_gap['gap_right']} — "
                f"there's a gap between what patients value and what they're advertising"
            )
            + "\n"
            + _ensure_period(
                "Most patients decide where to go before they call. "
                "If your messaging reflects what patients actually experience, you win more of those comparisons"
            )
        )

    # ── Section 4: What to do ─────────────────────────────────────────────
    action_words = _human_join(top_raw_phrases[:2]) or praise_phrase
    if action_words:
        sections.append(
            "Your Action This Month\n"
            + _ensure_period(
                f"Make sure the words \"{action_words}\" show up in your Google Business profile, "
                f"your website homepage, and in how you coach patients to leave reviews"
            )
            + "\n"
            + _ensure_period(
                "You don't need to say everything — just pick the one or two things patients already love and repeat them until they become your reputation"
            )
        )

    if not sections:
        return ""

    return "\n\n".join(sections)