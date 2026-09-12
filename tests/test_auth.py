from unittest.mock import MagicMock, patch

from termcall.auth import login, logout, signup, whoami
from termcall.session import Session


def test_signup_calls_sign_up_with_credentials():
    fake_client = MagicMock()
    with patch("termcall.auth.build_sync_client", return_value=fake_client):
        signup("me@example.com", "hunter2")
    fake_client.auth.sign_up.assert_called_once_with({"email": "me@example.com", "password": "hunter2"})


def test_login_persists_session_and_returns_it():
    fake_client = MagicMock()
    fake_client.auth.sign_in_with_password.return_value.session.access_token = "a"
    fake_client.auth.sign_in_with_password.return_value.session.refresh_token = "r"
    fake_client.auth.sign_in_with_password.return_value.user.email = "me@example.com"
    saved = {}
    with patch("termcall.auth.build_sync_client", return_value=fake_client), \
         patch("termcall.auth.save_session", lambda s: saved.update(session=s)):
        session = login("me@example.com", "hunter2")
    assert session == Session(access_token="a", refresh_token="r", email="me@example.com")
    assert saved["session"] == session


def test_logout_clears_session():
    cleared = {"called": False}
    with patch("termcall.auth.clear_session", lambda: cleared.update(called=True)):
        logout()
    assert cleared["called"] is True


def test_whoami_returns_stored_email():
    with patch("termcall.auth.authenticated_sync_client", return_value=(MagicMock(), Session("a", "r", "me@example.com"))):
        assert whoami() == "me@example.com"
