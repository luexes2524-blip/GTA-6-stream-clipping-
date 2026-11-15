create extension if not exists pgcrypto;

create table if not exists public.clips (
  id uuid primary key default gen_random_uuid(),
  title text not null default 'clip',
  source_url text,
  storage_path text,
  public_url text,
  created_at timestamptz not null default now()
);

alter table public.clips enable row level security;

create policy "Allow public read access to clip metadata"
on public.clips
for select
using (true);

create policy "Allow public insert access to clip metadata"
on public.clips
for insert
with check (true);

create policy "Allow public update access to clip metadata"
on public.clips
for update
using (true)
with check (true);
