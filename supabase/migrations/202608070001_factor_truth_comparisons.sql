-- Factor Lab truth-compare execution evidence.
-- scripts/run_truth_compare.py writes artifacts/supabase_sync_payload.json;
-- scripts/sync_truth_compare_to_supabase.py upserts that payload into this
-- table plus the existing public_dashboard_* display tables.
-- Idempotent: safe to run after 202607160001_factor_lab_dashboard.sql.

create table if not exists public.factor_truth_comparisons (
  id uuid primary key default gen_random_uuid(),
  task_id text,
  run_id text not null,
  factor_family text not null,
  factor_name text not null,
  criteria_source text,
  status text not null check (status in ('passed', 'failed', 'not_comparable')),
  decision text not null check (decision in ('accept', 'reject')),
  reason text,
  overlap_ratio numeric,
  exact_match_ratio numeric,
  max_abs_error numeric,
  compared_count integer,
  mismatch_count integer,
  payload jsonb not null default '{}'::jsonb,
  executed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

-- one row per executor run; re-syncing the same payload is a no-op
create unique index if not exists factor_truth_comparisons_run_id_key
  on public.factor_truth_comparisons (run_id);

create index if not exists factor_truth_comparisons_factor_idx
  on public.factor_truth_comparisons (factor_family, factor_name, executed_at desc);

create index if not exists factor_truth_comparisons_task_idx
  on public.factor_truth_comparisons (task_id);

alter table public.factor_truth_comparisons enable row level security;

drop policy if exists "public read truth comparisons" on public.factor_truth_comparisons;

-- verdicts and metrics are display data; mismatch samples stay in private
-- artifacts, not in this table.
create policy "public read truth comparisons"
  on public.factor_truth_comparisons for select
  to anon, authenticated
  using (true);

grant select on public.factor_truth_comparisons to anon, authenticated;
grant insert, update on public.factor_truth_comparisons to authenticated;
