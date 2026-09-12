# termcall/auth.py
"""Signup/login/logout/whoami — thin wrappers around the sync Supabase client plus
keyring session storage. None of these need an event loop.
"""

from __future__ import annotations

from termcall.session import Session, clear_session, save_session
from termcall.supabase_client import authenticated_sync_client, build_sync_client


def signup(email: str, password: str) -> None:
    client = build_sync_client()
    client.auth.sign_up({"email": email, "password": password})


def login(email: str, password: str) -> Session:
    client = build_sync_client()
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    session = Session(
        access_token=response.session.access_token,
        refresh_token=response.session.refresh_token,
        email=response.user.email,
    )
    save_session(session)
    return session


def logout() -> None:
    clear_session()


def whoami() -> str:
    _client, session = authenticated_sync_client()
    return session.email
