-- Local Competitor Intelligence (Phase 1) schema
-- Paste this entire file into Supabase SQL Editor later

create extension if not exists "pgcrypto";

-- =========================
-- businesses
-- =========================
create table if not exists public.businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  primary_domain text,
  city text,
  state text,
  country text default 'US',
  notes text,
  created_at timestamptz not null default now()
);

create index if not exists idx_businesses_created_at
  on public.businesses (created_at desc);

-- =========================
-- competitors
-- =========================
create table if not exists public.competitors (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null
    references public.businesses(id) on delete cascade,
  name text not null,
  website_url text,
  google_place_id text,
  google_maps_url text,
  created_at timestamptz not null default now(),
  constraint uq_competitor_business unique (business_id, name, website_url)
);

create index if not exists idx_competitors_business
  on public.competitors (business_id);

-- =========================
-- snapshots
-- =========================
create table if not exists public.snapshots (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null
    references public.businesses(id) on delete cascade,
  competitor_id uuid not null
    references public.competitors(id) on delete cascade,

  observed_at timestamptz not null default now(),

  google_rating numeric(3,2),
  google_review_count integer,

  offer_summary text,
  price_hint text,
  visibility_score numeric(6,2),
  notes text,
  raw jsonb,

  created_at timestamptz not null default now()
);

create index if not exists idx_snapshots_lookup
  on public.snapshots (business_id, competitor_id, observed_at desc);

create unique index if not exists uq_snapshot_once_per_time
  on public.snapshots (competitor_id, observed_at);

-- =========================
-- reports
-- =========================
create table if not exists public.reports (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null
    references public.businesses(id) on delete cascade,

  period_start date not null,
  period_end date not null,

  title text not null,
  status text not null default 'registered',

  storage_bucket text,
  storage_path text,

  summary text,
  created_at timestamptz not null default now()
);

create unique index if not exists uq_report_period
  on public.reports (business_id, period_start, period_end);

-- =========================
-- alerts
-- =========================
create table if not exists public.alerts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null
    references public.businesses(id) on delete cascade,
  competitor_id uuid
    references public.competitors(id) on delete set null,

  alert_type text not null,
  severity text not null default 'info',
  title text not null,
  message text not null,

  observed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists idx_alerts_business
  on public.alerts (business_id, observed_at desc);
