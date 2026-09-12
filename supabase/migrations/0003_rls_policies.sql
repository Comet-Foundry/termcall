-- supabase/migrations/0003_rls_policies.sql
alter table profiles enable row level security;
alter table rooms enable row level security;
alter table room_members enable row level security;
alter table signals enable row level security;

create function public.is_active_room_member(p_room_id uuid) returns boolean
language sql security definer set search_path = public stable as $$
  select exists (
    select 1 from room_members
    where room_id = p_room_id and user_id = auth.uid() and left_at is null
  )
$$;

create function public.is_room_member(p_room_id uuid) returns boolean
language sql security definer set search_path = public stable as $$
  select exists (
    select 1 from room_members
    where room_id = p_room_id and user_id = auth.uid()
  )
$$;

create policy "profiles readable by any authenticated user"
  on profiles for select
  to authenticated
  using (true);

create policy "rooms selectable by active or past members"
  on rooms for select
  to authenticated
  using (
    host_id = auth.uid()
    or public.is_room_member(rooms.id)
  );

create policy "rooms insertable by their host while open"
  on rooms for insert
  to authenticated
  with check (host_id = auth.uid() and status = 'open');

create policy "room_members selectable by active members of the same room"
  on room_members for select
  to authenticated
  using (public.is_active_room_member(room_members.room_id));

create policy "signals selectable by sender or recipient"
  on signals for select
  to authenticated
  using (auth.uid() in (sender_id, recipient_id));

create policy "signals insertable by sender or recipient"
  on signals for insert
  to authenticated
  with check (auth.uid() in (sender_id, recipient_id));
