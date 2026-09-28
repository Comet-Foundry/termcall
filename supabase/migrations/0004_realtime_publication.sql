-- supabase/migrations/0004_realtime_publication.sql
-- termcall/signaling.py's subscribe_room_members/subscribe_signals rely on Postgres
-- Realtime's postgres_changes INSERT broadcasts, which only fire for tables in the
-- supabase_realtime publication. The cloud project has these toggled on via the
-- dashboard, but that state was never captured as a migration, so a fresh
-- `supabase start` (or a fresh cloud project) silently breaks all call signaling:
-- room joins and WebRTC offers/answers/ICE candidates are written but never
-- delivered to the other participant.
alter publication supabase_realtime add table room_members;
alter publication supabase_realtime add table signals;
