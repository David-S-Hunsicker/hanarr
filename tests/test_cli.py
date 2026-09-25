from click.testing import CliRunner

import hanarr.cli as cli_mod
from hanarr.cli import cli
from hanarr.pipeline import LLMUnavailableError


def test_search_command_prints_a_clean_error_when_llm_is_unavailable(tmp_path, monkeypatch):
    """Regression test: `hanarr search` used to let LLMUnavailableError
    propagate as a raw Python traceback -- the dashboard and scheduler
    both get a clear one-line message for this, the CLI should too."""
    monkeypatch.setattr(
        cli_mod, "run_search_cycle",
        lambda session, settings, profile, llm: (_ for _ in ()).throw(
            LLMUnavailableError("Ollama isn't reachable at http://127.0.0.1:11434 — start Ollama and try again.")
        ),
    )
    monkeypatch.setattr(cli_mod, "build_llm_client", lambda cfg: object())

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(tmp_path / "config.yaml"), "search"])

    assert result.exit_code != 0
    assert "Ollama isn't reachable" in result.output
