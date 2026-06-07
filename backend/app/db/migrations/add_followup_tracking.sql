-- Follow-up tracking for cold email sequences and post-free-report sequences

-- Add follow-up columns to outreach_prospects (cold email Day-5 and Day-12)
ALTER TABLE outreach_prospects
    ADD COLUMN IF NOT EXISTS followup1_sent_at TIMESTAMPTZ,   -- Day-5
    ADD COLUMN IF NOT EXISTS followup2_sent_at TIMESTAMPTZ;   -- Day-12

-- Track post-free-report follow-ups (Day-5, Day-12, Day-21 after PDF delivered)
CREATE TABLE IF NOT EXISTS prospect_followup_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id  UUID NOT NULL,
    day          INT  NOT NULL,          -- 5, 12, or 21
    to_email     TEXT NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_id, day)
);

CREATE INDEX IF NOT EXISTS idx_prospect_followup_log_business
    ON prospect_followup_log (business_id);
