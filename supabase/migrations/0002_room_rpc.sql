-- supabase/migrations/0002_room_rpc.sql
create function join_room(p_code text) returns rooms
language plpgsql security definer set search_path = public as $$
declare r rooms;
declare active_count int;
begin
  select * into r from public.rooms where code = p_code and status = 'open' for update;
  if r.id is null then
    raise exception 'room not joinable';
  end if;

  if exists (
    select 1 from public.room_members
    where room_id = r.id and user_id = auth.uid() and left_at is null
  ) then
    return r; -- already an active member; idempotent
  end if;

  select count(*) into active_count from public.room_members
    where room_id = r.id and left_at is null;
  if active_count >= 4 then
    raise exception 'room full';
  end if;

  insert into public.room_members (room_id, user_id) values (r.id, auth.uid());
  return r;
end;
$$;

create function leave_room(p_room_id uuid) returns void
language plpgsql security definer set search_path = public as $$
declare active_count int;
begin
  perform id from public.rooms where id = p_room_id for update;

  update public.room_members set left_at = now()
    where room_id = p_room_id and user_id = auth.uid() and left_at is null;

  select count(*) into active_count from public.room_members
    where room_id = p_room_id and left_at is null;
  if active_count = 0 then
    update public.rooms set status = 'ended', ended_at = now() where id = p_room_id;
  end if;
end;
$$;
