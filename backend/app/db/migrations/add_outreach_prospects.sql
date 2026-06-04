-- Outreach prospects table for automated cold email pipeline
CREATE TABLE IF NOT EXISTS outreach_prospects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    place_id TEXT UNIQUE NOT NULL,
    business_name TEXT NOT NULL,
    category TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    website TEXT,
    phone TEXT,
    contact_email TEXT,
    reviews_count INT,
    rating FLOAT,
    top_competitor_name TEXT,
    top_competitor_reviews INT,
    draft_subject TEXT,
    draft_body TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    -- status values: discovered | draft_ready | approved | sent | bounced | skipped | converted
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outreach_prospects_status ON outreach_prospects(status);
CREATE INDEX IF NOT EXISTS idx_outreach_prospects_created_at ON outreach_prospects(created_at DESC);
