# termcall/supabase_client.py
"""Builds Supabase clients — sync for one-shot auth commands, async for the
Realtime-dependent call flow — and enforces the session refresh policy: every
authenticated command refreshes proactively on load; an invalid refresh token clears
the keyring entry so the CLI can prompt `termcall login` (design §10).
"""

from __future__ import annotations

import os

import click
from supabase import AsyncClient, Client, acreate_client, create_client
from supabase_auth.errors import AuthApiError

from termcall.session import Session, clear_session, load_session, save_session

SUPABASE_URL_ENV = "SUPABASE_URL"
SUPABASE_ANON_KEY_ENV = "SUPABASE_ANON_KEY"


class SessionExpiredError(Exception):
    """Raised when there is no stored session, or the refresh token is no longer valid."""


def _read_env() -> tuple[str, str]:
    url = os.environ.get(SUPABASE_URL_ENV)
    key = os.environ.get(SUPABASE_ANON_KEY_ENV)
    if not url or not key:
        raise click.ClickException(f"{SUPABASE_URL_ENV} and {SUPABASE_ANON_KEY_ENV} must be set.")
    return url, key


def build_sync_client() -> Client:
    url, key = _read_env()
    return create_client(url, key)


async def build_async_client() -> AsyncClient:
    url, key = _read_env()
    return await acreate_client(url, key)


def _rotate(session: Session, response) -> Session:
    return Session(
        access_token=response.session.access_token,
        refresh_token=response.session.refresh_token,
        email=session.email,
    )


def authenticated_sync_client() -> tuple[Client, Session]:
    session = load_session()
    if session is None:
        raise SessionExpiredError("no stored session")
    client = build_sync_client()
    try:
        response = client.auth.refresh_session(session.refresh_token)
    except AuthApiError as err:
        clear_session()
        raise SessionExpiredError(str(err)) from err
    refreshed = _rotate(session, response)
    save_session(refreshed)
    client.auth.set_session(refreshed.access_token, refreshed.refresh_token)
    return client, refreshed


async def authenticated_async_client() -> tuple[AsyncClient, Session]:
    session = load_session()
    if session is None:
        raise SessionExpiredError("no stored session")
    client = await build_async_client()
    try:
        response = await client.auth.refresh_session(session.refresh_token)
    except AuthApiError as err:
        clear_session()
        raise SessionExpiredError(str(err)) from err
    refreshed = _rotate(session, response)
    save_session(refreshed)
    await client.auth.set_session(refreshed.access_token, refreshed.refresh_token)
    return client, refreshed
