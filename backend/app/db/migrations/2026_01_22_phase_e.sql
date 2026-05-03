-- Phase E (Scheduled Reports) - DB Migration
-- File: backend/app/db/migrations/2026_01_22_phase_e.sql

-- Ensure required extension exists (Supabase usually has this already)
create extension if not exists pgcrypto;

-- 1) Report schedules (one per business for now)
create table if not exists public.report_schedules (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,

  frequency text not null check (frequency in ('weekly', 'monthly')),
  day_of_week int null check (day_of_week between 0 and 6),
  day_of_month int null check (day_of_month between 1 and 31),

  hour int not null default 9 check (hour between 0 and 23),
  minute int not null default 0 check (minute between 0 and 59),
  timezone text not null default 'America/New_York',

  is_enabled boolean not null default true,
  last_run_at timestamptz null,
  next_run_at timestamptz null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One schedule per business (simple v1)
create unique index if not exists report_schedules_business_id_uq
  on public.report_schedules (business_id);

create index if not exists report_schedules_next_run_idx
  on public.report_schedules (next_run_at);

-- 2) Generated reports (stored artifacts)
create table if not exists public.generated_reports (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  schedule_id uuid null references public.report_schedules(id) on delete set null,

  period_start timestamptz not null,
  period_end timestamptz not null,
  generated_at timestamptz not null default now(),

  status text not null default 'generated' check (status in ('generated', 'failed')),
  title text not null,
  summary_text text not null,

  sections jsonb not null default '{}'::jsonb,
  inputs jsonb not null default '{}'::jsonb,

  error text null
);

create index if not exists generated_reports_business_generated_at_idx
  on public.generated_reports (business_id, generated_at desc);

create index if not exists generated_reports_business_period_end_idx
  on public.generated_reports (business_id, period_end desc);
