# termcall/rooms.py
"""Room lifecycle: code generation, create/join/leave via the join_room/leave_room
RPCs (design §4.2), and active-roster lookups. Every function takes an authenticated
AsyncClient; error mapping matches design §10.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from postgrest.exceptions import APIError
from supabase import AsyncClient

ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # excludes 0/O, 1/I
ROOM_CODE_LENGTH = 6
MAX_CODE_ATTEMPTS = 5


@dataclass(frozen=True)
class Room:
    id: str
    code: str
    host_id: str
    status: str


@dataclass(frozen=True)
class Member:
    id: int
    room_id: str
    user_id: str
    joined_at: str
    left_at: str | None


class RoomNotJoinableError(Exception):
    """Raised when a room code doesn't exist or the room has already ended."""


class RoomFullError(Exception):
    """Raised when a room already has 4 active participants."""


def generate_room_code(length: int = ROOM_CODE_LENGTH) -> str:
    return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(length))


def parse_room(row: dict) -> Room:
    return Room(id=row["id"], code=row["code"], host_id=row["host_id"], status=row["status"])


def parse_member(row: dict) -> Member:
    return Member(
        id=row["id"],
        room_id=row["room_id"],
        user_id=row["user_id"],
        joined_at=row["joined_at"],
        left_at=row.get("left_at"),
    )


async def create_room(client: AsyncClient, host_id: str) -> Room:
    for _ in range(MAX_CODE_ATTEMPTS):
        code = generate_room_code()
        try:
            resp = await client.table("rooms").insert({"code": code, "host_id": host_id, "status": "open"}).execute()
        except APIError as err:
            if "duplicate key" in str(err).lower():
                continue
            raise
        room = parse_room(resp.data[0])
        break
    else:
        raise RuntimeError("could not allocate a unique room code")

    await client.rpc("join_room", {"p_code": room.code}).execute()
    return room


async def join_room(client: AsyncClient, code: str) -> Room:
    try:
        resp = await client.rpc("join_room", {"p_code": code}).execute()
    except APIError as err:
        message = str(err).lower()
        if "room full" in message:
            raise RoomFullError(str(err)) from err
        if "room not joinable" in message:
            raise RoomNotJoinableError(str(err)) from err
        raise
    return parse_room(resp.data)


async def leave_room(client: AsyncClient, room_id: str) -> None:
    await client.rpc("leave_room", {"p_room_id": room_id}).execute()


async def fetch_active_roster(client: AsyncClient, room_id: str) -> list[Member]:
    resp = (
        await client.table("room_members")
        .select("*")
        .eq("room_id", room_id)
        .is_("left_at", "null")
        .order("id")
        .execute()
    )
    return [parse_member(row) for row in resp.data]
