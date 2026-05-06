-- ============================================
-- DSE Lease Calc - Supabase Schema
-- Supabase SQL Editor에서 그대로 실행
-- ============================================

-- ============================================
-- 1) items: 품목 마스터 (사용자별)
-- ============================================
create table public.items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,

  -- 의료기기 분류
  -- 'skincare'        : 피부미용
  -- 'general_paid'    : 일반-급여 (한방수가)
  -- 'general_unpaid'  : 일반-비급여
  category text not null check (category in ('skincare', 'general_paid', 'general_unpaid')),

  name text not null,

  -- 마진 분석용
  supply_price   numeric default 0,  -- 공급가
  sale_price     numeric default 0,  -- 판매가
  package_price  numeric default 0,  -- 패키지 할인가

  -- 회당 시술가 (피부미용 / 비급여 자율가격)
  procedure_price numeric default 0,

  -- 급여(한방수가) 전용 - HIRA biz 검색결과 입력
  hira_code  text,
  hira_name  text,
  hira_price numeric,

  -- 메타
  sort_order int default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index items_user_id_idx  on public.items(user_id);
create index items_category_idx on public.items(category);

-- ============================================
-- 2) simulations: 시뮬레이션 세션
-- ============================================
create table public.simulations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,

  name text not null,
  category text check (category in ('skincare', 'general_paid', 'general_unpaid')),

  -- 리스 조건
  lease_term_months int     default 24,
  deposit_pct       numeric default 0,
  fee_pct           numeric default 0,   -- 선취수수료율
  rate_pct          numeric default 0,   -- 적용금리
  discount_pct      numeric default 0,   -- 할인율

  -- ROI 가정
  monthly_new_patients     int default 0,
  monthly_revisit_patients int default 0,
  procedure_price          numeric default 0,

  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index simulations_user_id_idx on public.simulations(user_id);

-- ============================================
-- 3) simulation_items: 세션-품목 연결
-- ============================================
create table public.simulation_items (
  simulation_id uuid not null references public.simulations(id) on delete cascade,
  item_id       uuid not null references public.items(id)       on delete cascade,
  quantity       int     default 1,
  price_override numeric,  -- 이 시뮬레이션에서만 가격 임의 조정
  primary key (simulation_id, item_id)
);

-- ============================================
-- updated_at 자동 갱신
-- ============================================
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger items_set_updated_at
  before update on public.items
  for each row execute function public.set_updated_at();

create trigger simulations_set_updated_at
  before update on public.simulations
  for each row execute function public.set_updated_at();

-- ============================================
-- RLS: 본인 데이터만 접근
-- ============================================
alter table public.items            enable row level security;
alter table public.simulations      enable row level security;
alter table public.simulation_items enable row level security;

-- items
create policy "items_select_own" on public.items for select using (auth.uid() = user_id);
create policy "items_insert_own" on public.items for insert with check (auth.uid() = user_id);
create policy "items_update_own" on public.items for update using (auth.uid() = user_id);
create policy "items_delete_own" on public.items for delete using (auth.uid() = user_id);

-- simulations
create policy "sims_select_own" on public.simulations for select using (auth.uid() = user_id);
create policy "sims_insert_own" on public.simulations for insert with check (auth.uid() = user_id);
create policy "sims_update_own" on public.simulations for update using (auth.uid() = user_id);
create policy "sims_delete_own" on public.simulations for delete using (auth.uid() = user_id);

-- simulation_items: 본인 시뮬레이션의 품목만
create policy "siitems_select_own" on public.simulation_items for select
  using (exists (select 1 from public.simulations s where s.id = simulation_items.simulation_id and s.user_id = auth.uid()));
create policy "siitems_insert_own" on public.simulation_items for insert
  with check (exists (select 1 from public.simulations s where s.id = simulation_items.simulation_id and s.user_id = auth.uid()));
create policy "siitems_update_own" on public.simulation_items for update
  using (exists (select 1 from public.simulations s where s.id = simulation_items.simulation_id and s.user_id = auth.uid()));
create policy "siitems_delete_own" on public.simulation_items for delete
  using (exists (select 1 from public.simulations s where s.id = simulation_items.simulation_id and s.user_id = auth.uid()));
