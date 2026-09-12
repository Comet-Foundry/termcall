"""Integration tests against a local Supabase instance (`supabase start`). Skipped
automatically unless SUPABASE_URL is set — these are not part of the default
`pytest` run (design §12).
"""

import os
import uuid

import pytest
from postgrest.exceptions import APIError
from supabase import Client, create_client

pytestmark = pytest.mark.integration

if not os.environ.get("SUPABASE_URL"):
    pytest.skip("SUPABASE_URL not set; run `supabase start` for RLS integration tests", allow_module_level=True)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]


def _signed_up_client() -> tuple[Client, str]:
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    email = f"{uuid.uuid4()}@example.test"
    password = "correct horse battery staple"
    response = client.auth.sign_up({"email": email, "password": password})
    client.auth.set_session(response.session.access_token, response.session.refresh_token)
    return client, response.user.id


@pytest.fixture
def users() -> list[tuple[Client, str]]:
    return [_signed_up_client() for _ in range(5)]


def test_fifth_join_to_a_full_room_is_rejected(users):
    host, _host_id = users[0]
    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": _host_id}).execute()
    code = room.data[0]["code"]
    host.rpc("join_room", {"p_code": code}).execute()

    for member, _uid in users[1:4]:
        member.rpc("join_room", {"p_code": code}).execute()

    fifth, _fifth_id = users[4]
    with pytest.raises(APIError, match="room full"):
        fifth.rpc("join_room", {"p_code": code}).execute()


def test_signals_are_not_readable_by_an_uninvolved_user(users):
    host, host_id = users[0]
    sender, sender_id = users[1]
    recipient, recipient_id = users[2]
    bystander, _bystander_id = users[3]

    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": host_id}).execute()
    room_id = room.data[0]["id"]
    host.rpc("join_room", {"p_code": room.data[0]["code"]}).execute()

    sender.table("signals").insert(
        {
            "room_id": room_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "kind": "offer",
            "payload": {"sdp": "v=0"},
        }
    ).execute()

    visible_to_recipient = recipient.table("signals").select("*").eq("sender_id", sender_id).execute()
    assert len(visible_to_recipient.data) == 1

    visible_to_bystander = bystander.table("signals").select("*").eq("sender_id", sender_id).execute()
    assert len(visible_to_bystander.data) == 0


def test_room_members_visible_only_to_active_members_of_that_room(users):
    host, host_id = users[0]
    outsider, _outsider_id = users[1]
    room = host.table("rooms").insert({"code": str(uuid.uuid4())[:8], "host_id": host_id}).execute()
    room_id = room.data[0]["id"]
    host.rpc("join_room", {"p_code": room.data[0]["code"]}).execute()

    visible_to_host = host.table("room_members").select("*").eq("room_id", room_id).execute()
    assert len(visible_to_host.data) == 1

    visible_to_outsider = outsider.table("room_members").select("*").eq("room_id", room_id).execute()
    assert len(visible_to_outsider.data) == 0
