# ports-menu

A menu bar view of every TCP port you're listening on — what launched it, where
from, and buttons to stop, restart, label, and reopen it. Built as a
[SwiftBar](https://swiftbar.app) plugin so every behavior is a visible line of
Python you can bend.

```
⇄ 7
 ├ 5432  docker · pgvector-db
 ├ 8000  Symphony launcher
 │   ├ symphony serve --no-open
 │   ├ pid 12256 · bound to 127.0.0.1
 │   ├ cwd ~/Developer/Architecture/symphony
 │   ├ Open localhost:8000
 │   ├ Stop / Force stop / Restart from recipe
 │   └ Label… / Copy command / Forget
 └ 8443  vite (⚠ all interfaces)
```

## Install

```bash
brew install swiftbar
```

Launch SwiftBar once and pick (or create) a plugin folder, then:

```bash
./install.sh            # symlinks the plugin into SwiftBar's plugin folder
```

or symlink by hand: `ln -s "$PWD/ports-menu.10s.py" <your plugin folder>/`.
The `10s` in the filename is the refresh cadence — rename to taste.

## How it works

- **Listing** — `lsof -iTCP -sTCP:LISTEN` for pid/port/bind, `ps` for the full
  launch command, `lsof -d cwd` for the working directory. Without sudo you
  only see your own processes; root-owned listeners won't appear.
- **Adoption** — the first time a listener is seen, its launch *recipe*
  (command, cwd, env snapshot) is saved to `~/.config/ports-menu/recipes.json`.
  **Restart from recipe** kills the process and replays the recipe through
  `zsh -lc` in the original cwd with the saved env merged over yours; output
  goes to `~/.config/ports-menu/logs/`. Recipes are editable JSON — fix one up
  by hand if a process needs more context than a snapshot can capture.
- **Labels** — `Label…` opens a dialog; names are keyed by `process:port` in
  `labels.json`, so they survive restarts and reboots.
- **Docker** — ports published by Docker all belong to `com.docker.backend`,
  so those rows are resolved to container names via `docker ps` and get
  `docker stop` / `docker restart` instead of signals.
- **Exposure** — rows bound to `0.0.0.0`/`*` (reachable from the network, not
  just localhost) are flagged ⚠ and tinted.

## Tailoring

`~/.config/ports-menu/config.json` (created on first run):

| key | meaning |
|---|---|
| `ignore_processes` | process names to hide (default: ControlCenter, rapportd, sharingd) |
| `ignore_ports` | ports to hide |
| `kill_grace_seconds` | how long Stop waits after SIGTERM before giving up (Restart then SIGKILLs) |
| `save_env` | snapshot environment into recipes |
| `env_redact` | regex of env var names never saved (secrets) |

Everything else is the plugin itself — `render()` draws the menu, `main()`
dispatches the actions, and each menu line is a plain `print`.

## Caveats

- The env snapshot (`ps eww`) is best-effort: values containing spaces parse
  greedily, and redacted vars are simply absent on restart. For a fussy
  process, edit its recipe in `recipes.json`.
- The command string from `ps` loses shell quoting; replay goes through
  `zsh -lc`, which is right for the common case but not for exotic quoting.
- Restart re-runs what the process *was started as* — if that was a wrapper
  (`npm run dev`), you get the wrapper again, which is usually what you want.
