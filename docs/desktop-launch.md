# Desktop launch foundation

`hanarr serve` remains the single backend entry point. The dashboard launch
mode can be selected in `config.yaml` under `dashboard.launch_mode` or per run
with `--launch-mode`:

- `none` keeps the existing foreground server behavior.
- `browser` starts the local server and opens the default browser.
- `webview` starts the same server in a desktop webview. Install the optional
  dependency with `pip install -e ".[desktop]"`.

The webview dependency is optional so CLI and browser use do not require desktop
tooling. The Windows packaging foundation adds packaged browser and desktop entry points
around this same contract. See [`windows-installer.md`](windows-installer.md)
for the PyInstaller/Inno Setup build; signing, release automation, and update
services remain deferred.
