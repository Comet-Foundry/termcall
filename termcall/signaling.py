# termcall/signaling.py
"""WebRTC signaling: payload shaping/parsing and offerer/answerer determinism
(design §6). Realtime subscriptions are added in Task 13.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from supabase import AsyncClient

from termcall.rooms import Member, parse_member


@dataclass(frozen=True)
class Signal:
    id: int
    room_id: str
    sender_id: str
    recipient_id: str
    kind: str  # "offer" | "answer" | "ice"
    payload: dict


def am_i_offerer(my_member_id: int, peer_member_id: int) -> bool:
    """`room_members.id` is an always-increasing identity column, so the row with the
    lower id joined first. Design §6: whoever joined earlier offers to whoever joined
    later. Ids are globally unique, so this never ties.
    """
    if my_member_id == peer_member_id:
        raise ValueError("a member cannot be compared against itself")
    return my_member_id < peer_member_id


def build_offer_payload(sdp: str) -> dict:
    return {"sdp": sdp}


def build_answer_payload(sdp: str) -> dict:
    return {"sdp": sdp}


def build_ice_payload(candidate: str, sdp_mid: str | None, sdp_mline_index: int | None) -> dict:
    return {"candidate": candidate, "sdpMid": sdp_mid, "sdpMLineIndex": sdp_mline_index}


def parse_signal(row: dict) -> Signal:
    return Signal(
        id=row["id"],
        room_id=row["room_id"],
        sender_id=row["sender_id"],
        recipient_id=row["recipient_id"],
        kind=row["kind"],
        payload=row["payload"],
    )


async def insert_signal(
    client: AsyncClient, *, room_id: str, sender_id: str, recipient_id: str, kind: str, payload: dict
) -> None:
    await client.table("signals").insert(
        {
            "room_id": room_id,
            "sender_id": sender_id,
            "recipient_id": recipient_id,
            "kind": kind,
            "payload": payload,
        }
    ).execute()


async def subscribe_room_members(
    client: AsyncClient, room_id: str, on_member: Callable[[Member], Awaitable[None]]
):
    """SELECT the current active roster, then subscribe to future INSERTs — a member
    joining mid-call must see who's already there (design §6).
    """
    backlog_resp = (
        await client.table("room_members").select("*").eq("room_id", room_id).is_("left_at", "null").execute()
    )
    backlog = [parse_member(row) for row in backlog_resp.data]

    def _on_insert(payload: dict) -> None:
        member = parse_member(payload["data"]["record"])
        asyncio.ensure_future(on_member(member))

    channel = client.channel(f"room_members:{room_id}")
    channel.on_postgres_changes(
        "INSERT", schema="public", table="room_members", filter=f"room_id=eq.{room_id}", callback=_on_insert
    )
    await channel.subscribe()
    return backlog, channel


async def subscribe_signals(
    client: AsyncClient, room_id: str, my_user_id: str, on_signal: Callable[[Signal], Awaitable[None]]
):
    """SELECT signals already addressed to me, then subscribe to future INSERTs."""
    backlog_resp = (
        await client.table("signals")
        .select("*")
        .eq("room_id", room_id)
        .eq("recipient_id", my_user_id)
        .execute()
    )
    backlog = [parse_signal(row) for row in backlog_resp.data]

    def _on_insert(payload: dict) -> None:
        signal = parse_signal(payload["data"]["record"])
        asyncio.ensure_future(on_signal(signal))

    channel = client.channel(f"signals:{my_user_id}")
    channel.on_postgres_changes(
        "INSERT", schema="public", table="signals", filter=f"recipient_id=eq.{my_user_id}", callback=_on_insert
    )
    await channel.subscribe()
    return backlog, channel
