"""Uses an in-memory fake keyring backend throughout -- never touches the
real OS credential store during tests."""
import keyring
import keyring.errors
import pytest

from hanarr import secrets_store


class _FakeKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


class _FailingKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        raise keyring.errors.KeyringError("no backend available")

    def set_password(self, service, username, password):
        raise keyring.errors.KeyringError("no backend available")

    def delete_password(self, service, username):
        raise keyring.errors.KeyringError("no backend available")


@pytest.fixture
def fake_backend(monkeypatch):
    backend = _FakeKeyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


def test_set_then_get_roundtrips_the_secret(fake_backend):
    assert secrets_store.set_secret(secrets_store.ANTHROPIC_API_KEY, "sk-ant-abc123") is True
    assert secrets_store.get_secret(secrets_store.ANTHROPIC_API_KEY) == "sk-ant-abc123"


def test_get_secret_returns_none_when_never_set(fake_backend):
    assert secrets_store.get_secret(secrets_store.ANTHROPIC_API_KEY) is None


def test_clear_secret_removes_it(fake_backend):
    secrets_store.set_secret(secrets_store.SMTP_PASSWORD, "hunter2")
    secrets_store.clear_secret(secrets_store.SMTP_PASSWORD)
    assert secrets_store.get_secret(secrets_store.SMTP_PASSWORD) is None


def test_clear_secret_that_was_never_set_does_not_raise(fake_backend):
    secrets_store.clear_secret(secrets_store.SMTP_PASSWORD)  # no-op, must not raise


def test_get_secret_degrades_to_none_when_no_backend_is_available(monkeypatch):
    """Regression test: a missing/broken keyring backend (e.g. a minimal
    Linux box with no Secret Service running) must never crash the app --
    callers already have an env-var fallback for this case."""
    monkeypatch.setattr(keyring, "get_password", _FailingKeyring().get_password)
    assert secrets_store.get_secret(secrets_store.ANTHROPIC_API_KEY) is None


def test_set_secret_degrades_to_false_when_no_backend_is_available(monkeypatch):
    monkeypatch.setattr(keyring, "set_password", _FailingKeyring().set_password)
    assert secrets_store.set_secret(secrets_store.ANTHROPIC_API_KEY, "x") is False


def test_clear_secret_degrades_silently_when_no_backend_is_available(monkeypatch):
    monkeypatch.setattr(keyring, "delete_password", _FailingKeyring().delete_password)
    secrets_store.clear_secret(secrets_store.ANTHROPIC_API_KEY)  # must not raise
