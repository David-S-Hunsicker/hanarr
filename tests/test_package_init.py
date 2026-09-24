"""Regression tests for hanarr's OS-trust-store TLS fix
(hanarr._use_os_trust_store_for_tls, called once at package import time).

Real-world context: on a machine running TLS-inspecting security/network
software (confirmed live on the development machine -- endpoint monitoring
software installs its own root CA into the OS trust store, which curl and
browsers pick up automatically, but which certifi's bundled CA list does
not include), every httpx-based HTTPS call in this app -- every job
connector, the GitHub submission adapter, Ollama setup, the update checker
-- was silently failing its TLS handshake. Each call site's own error
handling (deliberately, so one bad source doesn't kill a whole search
cycle) swallowed the failure completely, which looked exactly like "no
postings matched" with no error anywhere. Verified live: the same
Greenhouse endpoint returned 0 results before this fix and real data
immediately after, with no other change.
"""
from __future__ import annotations

import hanarr


def test_use_os_trust_store_calls_truststore_inject(monkeypatch):
    calls = []
    monkeypatch.setattr("truststore.inject_into_ssl", lambda: calls.append(True))

    hanarr._use_os_trust_store_for_tls()

    assert calls == [True]


def test_use_os_trust_store_never_raises_if_truststore_fails(monkeypatch):
    """Startup must never be blocked by this -- a broken/missing truststore
    should degrade to the pre-fix (certifi-only) behavior, not prevent the
    app from running at all."""
    def boom():
        raise RuntimeError("simulated truststore failure")

    monkeypatch.setattr("truststore.inject_into_ssl", boom)

    hanarr._use_os_trust_store_for_tls()  # must not raise
