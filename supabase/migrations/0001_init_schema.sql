-- supabase/migrations/0001_init_schema.sql
create table profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text unique not null,
  created_at timestamptz not null default now()
);

create table rooms (
  id          uuid primary key default gen_random_uuid(),
  code        text unique not null,
  host_id     uuid not null references profiles(id),
  status      text not null default 'open'
              check (status in ('open', 'ended')),
  created_at  timestamptz not null default now(),
  ended_at    timestamptz
);

create table room_members (
  id         bigint generated always as identity primary key,
  room_id    uuid not null references rooms(id) on delete cascade,
  user_id    uuid not null references profiles(id),
  joined_at  timestamptz not null default now(),
  left_at    timestamptz
);

create table signals (
  id           bigint generated always as identity primary key,
  room_id      uuid not null references rooms(id) on delete cascade,
  sender_id    uuid not null references profiles(id),
  recipient_id uuid not null references profiles(id),
  kind         text not null check (kind in ('offer', 'answer', 'ice')),
  payload      jsonb not null,
  created_at   timestamptz not null default now()
);

create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email);
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
