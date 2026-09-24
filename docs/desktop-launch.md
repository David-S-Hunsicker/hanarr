# Desktop launch foundation

`jobcopilot serve` remains the single backend entry point. The dashboard launch
mode can be selected in `config.yaml` under `dashboard.launch_mode` or per run
with `--launch-mode`:

- `none` keeps the existing foreground server behavior.
- `browser` starts the local server and opens the default browser.
- `webview` starts the same server in a desktop webview. Install the optional
  dependency with `pip install -e ".[desktop]"`.

The webview dependency is optional so CLI and browser use do not require desktop
tooling. This is a launch abstraction for a future packaged Windows runtime,
not an installer: no installer artifact, runtime bundle, shortcut registration,
Ollama installation, or update service is implemented yet.
