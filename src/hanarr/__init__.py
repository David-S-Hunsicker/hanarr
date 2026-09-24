__version__ = "0.1.0"


def _use_os_trust_store_for_tls() -> None:
    """Use the OS certificate store for TLS verification instead of the
    certifi bundle httpx/urllib3 ship by default.

    This matters on machines running TLS-inspecting security/network
    software (antivirus, endpoint monitoring, some VPN/proxy tools) that
    installs its own root CA into the OS trust store so ordinary apps
    (browsers, curl) verify fine -- but that root CA isn't in certifi's
    bundled list, so every HTTPS call from every connector, the GitHub
    submission adapter, Ollama setup, and the update checker was silently
    failing its TLS handshake and being swallowed by each call site's own
    error handling (by design, so one bad source doesn't kill a whole
    search cycle) -- which looked exactly like "no postings matched" with
    no visible error anywhere. A broken/missing truststore must never
    block startup; it just degrades to the pre-fix (certifi-only)
    behavior."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - never let this block startup
        pass


# Importing this package is the one thing every entry point (CLI, the
# packaged desktop/browser launchers, the test suite) already does, so
# it's the one place this can be applied unconditionally, as early as
# possible, exactly once.
_use_os_trust_store_for_tls()
