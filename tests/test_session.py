from unittest.mock import patch

import keyring.errors

from termcall.session import Session, clear_session, load_session, save_session


def test_round_trip_save_then_load():
    store: dict[str, str] = {}
    with patch("termcall.session.keyring.set_password", lambda service, user, value: store.__setitem__((service, user), value)), \
         patch("termcall.session.keyring.get_password", lambda service, user: store.get((service, user))):
        session = Session(access_token="a", refresh_token="r", email="me@example.com")
        save_session(session)
        assert load_session() == session


def test_load_returns_none_when_nothing_stored():
    with patch("termcall.session.keyring.get_password", return_value=None):
        assert load_session() is None


def test_clear_session_swallows_missing_entry():
    with patch("termcall.session.keyring.delete_password", side_effect=keyring.errors.PasswordDeleteError):
        clear_session()  # must not raise
