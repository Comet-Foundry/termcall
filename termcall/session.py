"""OS-keyring-backed session storage. Tokens are opaque strings we never log; only
the email is safe to print (`termcall whoami`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import keyring
import keyring.errors

KEYRING_SERVICE = "termcall"
KEYRING_USERNAME = "session"


@dataclass(frozen=True)
class Session:
    access_token: str
    refresh_token: str
    email: str


def save_session(session: Session) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, json.dumps(asdict(session)))


def load_session() -> Session | None:
    raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if raw is None:
        return None
    return Session(**json.loads(raw))


def clear_session() -> None:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
