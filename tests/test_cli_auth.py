from unittest.mock import patch

from click.testing import CliRunner

from main import cli
from termcall.session import Session
from termcall.supabase_client import SessionExpiredError


def test_signup_prompts_and_calls_auth_signup():
    with patch("termcall.auth.signup") as mock_signup:
        result = CliRunner().invoke(cli, ["signup"], input="me@example.com\nhunter2\nhunter2\n")
    assert result.exit_code == 0
    mock_signup.assert_called_once_with("me@example.com", "hunter2")


def test_login_prompts_and_prints_confirmation():
    with patch("termcall.auth.login", return_value=Session("a", "r", "me@example.com")) as mock_login:
        result = CliRunner().invoke(cli, ["login"], input="me@example.com\nhunter2\n")
    assert result.exit_code == 0
    assert "me@example.com" in result.output
    mock_login.assert_called_once_with("me@example.com", "hunter2")


def test_whoami_reports_expired_session_as_click_exception():
    with patch("termcall.auth.whoami", side_effect=SessionExpiredError("no session")):
        result = CliRunner().invoke(cli, ["whoami"])
    assert result.exit_code != 0
    assert "termcall login" in result.output


def test_logout_calls_auth_logout():
    with patch("termcall.auth.logout") as mock_logout:
        result = CliRunner().invoke(cli, ["logout"])
    assert result.exit_code == 0
    mock_logout.assert_called_once()
