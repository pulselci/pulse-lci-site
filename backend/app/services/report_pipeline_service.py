def build_full_report_pipeline(business_id: UUID):

    # 1. ALWAYS collect fresh data
    ingest_reviews_for_business(str(business_id))
    collect_snapshots_for_business(business_id)

    # 2. Core data
    deltas = compute_snapshot_deltas(business_id=business_id)

    # 3. Core sections
    sections = {}

    sections["momentum"] = deltas

    sections["share_of_voice"] = build_share_of_voice(...)
    sections["review_count_bar"] = build_review_count_bar(...)
    sections["review_pulse"] = _build_review_pulse_payload(business_id)

    # 4. Baseline insights
    baseline = build_baseline_insights(...)

    # 5. Perception + review engine (CRITICAL)
    review_payload = build_review_insights_for_business(business_id)

    sections["customer_perception"] = review_payload.get("customer_perception", [])
    sections["customer_friction"] = review_payload.get("customer_friction", [])
    sections["money_insights"] = review_payload.get("money_insights", [])

    # 6. Combine insights
    all_insights = []
    all_insights.extend(baseline)
    all_insights.extend(sections["customer_perception"])
    all_insights.extend(sections["customer_friction"])
    all_insights.extend(sections["money_insights"])

    sections["insights"] = all_insights

    # 7. Presentation layer (THIS DRIVES YOUR REPORT)
    sections["report_experience"] = _build_report_experience_payload(
        all_insights,
        sections=sections
    )

    # 8. Guardrails
    sections["report_experience"] = ensure_report_experience_guardrails(
        sections["report_experience"],
        sections=sections
    )

    return sections