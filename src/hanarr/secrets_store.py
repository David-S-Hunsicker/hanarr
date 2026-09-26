"""Stores sensitive credentials (API keys, SMTP passwords) in the OS's own
credential store via `keyring` (Windows Credential Manager, macOS Keychain,
Linux Secret Service) instead of in config.yaml or .env.

Unlike a password, an API key must be recoverable in plaintext -- the app
has to send the real value to the provider on every request -- so this is
encryption at rest via the OS, not a one-way hash. A locally stored
encryption key sitting in another file wouldn't add real protection (anyone
who can read config.yaml could typically read that file too); the OS
credential store instead ties the secret to the logged-in user account.

Every function degrades to a no-op/None rather than raising if no keyring
backend is available (e.g. a minimal Linux box with no Secret Service
running) -- callers already fall back to an environment variable in that
case, so a missing backend should never crash the app.
"""
from __future__ import annotations

import logging

import keyring
import keyring.errors

logger = logging.getLogger(__name__)

SERVICE_NAME = "hanarr"

ANTHROPIC_API_KEY = "anthropic_api_key"
SMTP_PASSWORD = "smtp_password"


def get_secret(name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE_NAME, name)
    except keyring.errors.KeyringError as exc:
        logger.warning("Keyring unavailable, could not read %r: %s", name, exc)
        return None


def set_secret(name: str, value: str) -> bool:
    """Returns True on success, False if no keyring backend is available."""
    try:
        keyring.set_password(SERVICE_NAME, name, value)
        return True
    except keyring.errors.KeyringError as exc:
        logger.warning("Keyring unavailable, could not store %r: %s", name, exc)
        return False


def clear_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE_NAME, name)
    except keyring.errors.PasswordDeleteError:
        pass  # already absent -- clearing an unset secret is a no-op
    except keyring.errors.KeyringError as exc:
        logger.warning("Keyring unavailable, could not clear %r: %s", name, exc)
