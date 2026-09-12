# termcall/signaling.py
"""WebRTC signaling: payload shaping/parsing and offerer/answerer determinism
(design §6). Realtime subscriptions are added in Task 13.
"""

from __future__ import annotations

from dataclasses import dataclass

from supabase import AsyncClient


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
