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

        for item in items:
            t = str(item.get("type") or "").strip()
            s = _clean(item.get("summary"))

            if not s:
                continue

            if t == "praise_themes":
                praise = _clean(s.split(" for ", 1)[-1] if " for " in s else s)

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

    market_sentences: list[str] = []

    if competitor_phrase and owner_phrase:
        market_sentences.append(
            _ensure_period(
                f"Across the market, competitors are most visibly winning on {competitor_phrase}, while your clearest opening is {owner_phrase}"
            )
        )
        market_sentences.append(
            _ensure_period(
                f"Win by making {owner_phrase} the clear reason customers choose you over competitors focused on {competitor_phrase}"
            )
        )
    elif competitor_phrase:
        market_sentences.append(
            _ensure_period(
                f"Across the market, competitors are most visibly winning on {competitor_phrase}"
            )
        )
    elif owner_phrase:
        market_sentences.append(
            _ensure_period(
                f"Your clearest positioning opportunity is to make {owner_phrase} more visible before buyers compare alternatives"
            )
        )
    elif praise_phrase:
        market_sentences.append(
            _ensure_period(
                f"Customer perception in this market is centered around {praise_phrase}"
            )
        )

    if market_sentences:
        sections.append(
            "Market Story\n"
            + "\n".join([s.strip() for s in market_sentences])
        )

    # Strongest competitive callouts only
    ranked_rows = sorted(
        competitor_rows,
        key=lambda r: (
            1 if r.get("comp_strength") and r.get("owner_strength") else 0,
            len(str(r.get("comp_strength") or "")),
            len(str(r.get("praise") or "")),
        ),
        reverse=True,
    )

    callout_lines: list[str] = []

    for row in ranked_rows[:3]:
        competitor_name = row["competitor_name"]
        comp_strength = row.get("comp_strength")
        owner_strength = row.get("owner_strength")
        praise = row.get("praise")

        if comp_strength and owner_strength:
            callout_lines.append(
                _ensure_period(
                    f"{competitor_name}: wins on {comp_strength}. Counter with {owner_strength}"
                )
            )
        elif praise:
            callout_lines.append(
                _ensure_period(
                    f"{competitor_name} is most associated with {praise}"
                )
            )

    if callout_lines:
        sections.append(
            "Competitive Readout\n"
            + "\n".join([s.strip() for s in callout_lines])
        )

    
    if messaging_gap_rows:
        gap = messaging_gap_rows[0]
        sections.append(
            "Messaging Gap\n"
            + _ensure_period(
                f"{gap['competitor_name']} shows a messaging disconnect: reviews emphasize {gap['gap_left']}, while website messaging emphasizes {gap['gap_right']}"
            )
            + "\n"
            + _ensure_period(
                "This matters because buyers may decide before contacting the business if the visible message does not match what customers actually value"
            )
        )

    if owner_phrase:
        sections.append(
            "Recommended Position\n"
            + _ensure_period(
                f"Make {owner_phrase} the core message across homepage copy, service descriptions, review requests, and follow-up messaging"
            )
            + "\n"
            + _ensure_period(
                "The goal is not to mention every strength, but to repeat the strongest buyer-facing advantage until it becomes easy to remember"
            )
        )

    if not sections:
        return ""

    return "Customer Perception Insights\n\n" + "\n\n".join(sections)