# tests/test_supabase_client.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from supabase_auth.errors import AuthApiError

from termcall.session import Session
from termcall.supabase_client import SessionExpiredError, authenticated_async_client, authenticated_sync_client


def _fake_refresh_response(access="new-access", refresh="new-refresh"):
    resp = MagicMock()
    resp.session.access_token = access
    resp.session.refresh_token = refresh
    return resp


def test_authenticated_sync_client_refreshes_and_persists(monkeypatch):
    stored = Session(access_token="old", refresh_token="old-refresh", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    saved = {}
    monkeypatch.setattr("termcall.supabase_client.save_session", lambda s: saved.update(session=s))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.refresh_session.return_value = _fake_refresh_response()
    with patch("termcall.supabase_client.create_client", return_value=fake_client):
        client, session = authenticated_sync_client()

    assert client is fake_client
    assert session.access_token == "new-access"
    assert session.email == "me@example.com"
    assert saved["session"].access_token == "new-access"
    fake_client.auth.refresh_session.assert_called_once_with("old-refresh")


def test_authenticated_sync_client_clears_session_on_invalid_refresh_token(monkeypatch):
    stored = Session(access_token="old", refresh_token="bad", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    cleared = {"called": False}
    monkeypatch.setattr("termcall.supabase_client.clear_session", lambda: cleared.update(called=True))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.refresh_session.side_effect = AuthApiError("invalid refresh token", 401, "invalid_grant")
    with patch("termcall.supabase_client.create_client", return_value=fake_client):
        with pytest.raises(SessionExpiredError):
            authenticated_sync_client()

    assert cleared["called"] is True


def test_authenticated_sync_client_raises_when_nothing_stored(monkeypatch):
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    with pytest.raises(SessionExpiredError):
        authenticated_sync_client()


async def test_authenticated_async_client_refreshes_and_persists(monkeypatch):
    stored = Session(access_token="old", refresh_token="old-refresh", email="me@example.com")
    monkeypatch.setattr("termcall.supabase_client.load_session", lambda: stored)
    saved = {}
    monkeypatch.setattr("termcall.supabase_client.save_session", lambda s: saved.update(session=s))
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    fake_client = MagicMock()
    fake_client.auth.set_session = AsyncMock()
    fake_client.auth.refresh_session = AsyncMock(return_value=_fake_refresh_response())
    with patch("termcall.supabase_client.acreate_client", new=AsyncMock(return_value=fake_client)):
        client, session = await authenticated_async_client()

    assert client is fake_client
    assert session.access_token == "new-access"
    assert saved["session"].access_token == "new-access"
