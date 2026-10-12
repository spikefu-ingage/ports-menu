#!/usr/bin/env python3
"""ports-menu: a SwiftBar plugin that shows every TCP port you are listening on,
with the launch command behind it, and lets you label, stop, restart, and reopen them.

Rendered every 10s (rename the file to change the cadence). Invoked by SwiftBar with
arguments, it performs an action instead of rendering:

    ports-menu.10s.py stop PID | force PID | restart KEY PID | label KEY
                      copy KEY | log KEY | forget KEY | docker-stop NAME | docker-restart NAME

State lives in ~/.config/ports-menu/:
    config.json   ignore lists, kill grace, env capture toggle
    labels.json   key -> your label ("Symphony launcher")
    recipes.json  key -> {cmd, cwd, env} captured when a listener is first seen ("adoption"),
                  replayed on restart. Keys are "<process>:<port>".
    logs/         stdout+stderr of processes this plugin restarted

Only your own processes are visible without sudo; root-owned listeners will not appear.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "ports-menu"
LOG_DIR = CONFIG_DIR / "logs"
PLUGIN = Path(__file__).resolve()
DEFAULT_CONFIG = {
    "ignore_processes": ["ControlCenter", "rapportd", "sharingd"],
    "ignore_ports": [],
    "kill_grace_seconds": 5,
    "save_env": True,
    "env_redact": "TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|CREDENTIAL|PRIVATE",
}

os.environ["PATH"] = os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"


def sh(*args: str, text: str | None = None) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, input=text, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load(name: str, default):
    p = CONFIG_DIR / name
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except ValueError:
        return default


def store(name: str, data) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    p = CONFIG_DIR / name
    p.write_text(json.dumps(data, indent=1, sort_keys=True))
    p.chmod(0o600)


def config() -> dict:
    cfg = {**DEFAULT_CONFIG, **load("config.json", {})}
    if not (CONFIG_DIR / "config.json").exists():
        store("config.json", DEFAULT_CONFIG)
    return cfg


# ---------------------------------------------------------------------------
# what is listening
# ---------------------------------------------------------------------------
def listeners() -> list[dict]:
    """[{pid, name, port, bind}] deduped per (pid, port), sorted by port."""
    out, pid, name = {}, None, ""
    for line in sh("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn").splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid = int(val)
        elif tag == "c":
            name = val
        elif tag == "n" and pid is not None:
            bind, _, port = val.rpartition(":")
            if port.isdigit():
                out.setdefault((pid, int(port)), {"pid": pid, "name": name, "port": int(port), "bind": bind})
    return sorted(out.values(), key=lambda x: (x["port"], x["pid"]))


def docker_names() -> dict[int, str]:
    """host port -> container name, when Docker is around."""
    out = {}
    for line in sh("docker", "ps", "--format", "{{.Names}}\t{{.Ports}}").splitlines():
        cname, _, ports = line.partition("\t")
        for m in re.finditer(r":(\d+)->", ports):
            out[int(m.group(1))] = cname
    return out


def command_of(pid: int) -> str:
    return sh("ps", "-o", "command=", "-p", str(pid)).strip()


def cwd_of(pid: int) -> str:
    for line in sh("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn").splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def env_of(pid: int, plain_cmd: str, redact: str) -> dict[str, str]:
    """Best-effort env snapshot: `ps eww` appends VAR=val tokens after the command."""
    full = sh("ps", "eww", "-o", "command=", "-p", str(pid)).strip()
    if not full.startswith(plain_cmd):
        return {}
    env, key = {}, None
    rx_var, rx_redact = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*="), re.compile(redact, re.I)
    for tok in full[len(plain_cmd):].strip().split(" "):
        if rx_var.match(tok):
            key, _, val = tok.partition("=")
            env[key] = val
        elif key:
            env[key] += " " + tok
    return {k: v for k, v in env.items() if not rx_redact.search(k)}


def key_of(name: str, port: int) -> str:
    return f"{name}:{port}"


def adopt(rows: list[dict], cfg: dict) -> dict:
    """Save a launch recipe the first time a listener is seen, so restart knows what to replay."""
    recipes = load("recipes.json", {})
    changed = False
    for r in rows:
        k = key_of(r["name"], r["port"])
        if k in recipes:
            continue
        cmd = command_of(r["pid"])
        if not cmd:
            continue
        recipes[k] = {"cmd": cmd, "cwd": cwd_of(r["pid"]),
                      "env": env_of(r["pid"], cmd, cfg["env_redact"]) if cfg["save_env"] else {},
                      "adopted_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        changed = True
    if changed:
        store("recipes.json", recipes)
    return recipes


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------
def act(label: str, *params: str, refresh: bool = True, alt: bool = False) -> str:
    """A menu line that re-invokes this plugin with arguments."""
    ps = " ".join(f'param{i + 1}="{p}"' for i, p in enumerate(params))
    return (f'{label} | bash="{PLUGIN}" {ps} terminal=false'
            + (" refresh=true" if refresh else "") + (" alternate=true" if alt else ""))


def render() -> None:
    cfg = config()
    rows = [r for r in listeners()
            if r["name"] not in cfg["ignore_processes"] and r["port"] not in cfg["ignore_ports"]]
    docker = docker_names() if any("docker" in r["name"].lower() for r in rows) else {}
    labels = load("labels.json", {})
    recipes = adopt([r for r in rows if "docker" not in r["name"].lower()], cfg)

    print(f"⇄ {len(rows)}")
    print("---")
    if not rows:
        print("Nothing is listening (that this user can see)")
    for r in rows:
        k = key_of(r["name"], r["port"])
        exposed = r["bind"] in ("*", "0.0.0.0", "[::]")
        dname = docker.get(r["port"]) if "docker" in r["name"].lower() else None
        label = labels.get(k) or (f"docker · {dname}" if dname else r["name"])
        badge = "  ⚠ all interfaces" if exposed else ""
        print(f'{r["port"]:>5}  {label}{badge} | font=Menlo size=12' + (" color=#c47f00" if exposed else ""))
        rec = recipes.get(k, {})
        cmd = rec.get("cmd") or command_of(r["pid"])
        print(f'-- {cmd[:90]} | font=Menlo size=11')
        print(f'-- pid {r["pid"]} · bound to {r["bind"]}')
        if rec.get("cwd"):
            print(f'-- cwd {rec["cwd"].replace(str(Path.home()), "~")} | bash=/usr/bin/open param1="{rec["cwd"]}" terminal=false')
        print(f'-- Open localhost:{r["port"]} | href=http://localhost:{r["port"]}/')
        print("-----")
        if dname:
            print("-- " + act(f"Stop container {dname}", "docker-stop", dname))
            print("-- " + act(f"Restart container {dname}", "docker-restart", dname))
        else:
            print("-- " + act("Stop", "stop", str(r["pid"])))
            print("-- " + act("Force stop", "force", str(r["pid"]), alt=True))
            if rec.get("cmd"):
                print("-- " + act("Restart from recipe", "restart", k, str(r["pid"])))
        print("-----")
        print("-- " + act("Label…", "label", k))
        print("-- " + act("Copy command", "copy", k, refresh=False))
        log = LOG_DIR / f"{re.sub(r'[^A-Za-z0-9._-]', '_', k)}.log"
        if log.exists():
            print(f'-- View restart log | bash=/usr/bin/open param1="{log}" terminal=false')
        print("-- " + act("Forget recipe & label", "forget", k, alt=True))
    print("---")
    print(f'Open config folder | bash=/usr/bin/open param1="{CONFIG_DIR}" terminal=false')
    print("Refresh | refresh=true")


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------
def stop_pid(pid: int, grace: float, then_kill: bool) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        notify(f"No permission to stop pid {pid} (owned by another user)")
        return False
    deadline = time.time() + grace
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)
    if then_kill:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True
    notify(f"pid {pid} ignored SIGTERM; use Force stop")
    return False


def notify(text: str) -> None:
    sh("osascript", "-e", f'display notification "{text}" with title "ports-menu"')


def main(argv: list[str]) -> None:
    if not argv:
        return render()
    cfg = config()
    action, args = argv[0], argv[1:]
    recipes = load("recipes.json", {})
    if action == "stop":
        stop_pid(int(args[0]), cfg["kill_grace_seconds"], then_kill=False)
    elif action == "force":
        stop_pid(int(args[0]), 0.5, then_kill=True)
    elif action == "restart":
        k, pid = args[0], int(args[1])
        rec = recipes.get(k)
        if not rec:
            return notify(f"No recipe saved for {k}")
        if not stop_pid(pid, cfg["kill_grace_seconds"], then_kill=True):
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_DIR / f"{re.sub(r'[^A-Za-z0-9._-]', '_', k)}.log", "ab")
        env = {**os.environ, **rec.get("env", {})}
        subprocess.Popen(["/bin/zsh", "-lc", rec["cmd"]], cwd=rec.get("cwd") or None, env=env,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        notify(f"Restarted {k}")
    elif action == "label":
        k = args[0]
        labels = load("labels.json", {})
        current = labels.get(k, "").replace('"', "'")
        out = sh("osascript", "-e",
                 f'display dialog "Label for {k}" default answer "{current}" with title "ports-menu"')
        m = re.search(r"text returned:(.*)$", out.strip())
        if m is not None:
            labels[k] = m.group(1)
            if not labels[k]:
                labels.pop(k, None)
            store("labels.json", labels)
    elif action == "copy":
        rec = recipes.get(args[0], {})
        sh("pbcopy", text=rec.get("cmd", args[0]))
    elif action == "forget":
        recipes.pop(args[0], None)
        store("recipes.json", recipes)
        labels = load("labels.json", {})
        labels.pop(args[0], None)
        store("labels.json", labels)
    elif action == "docker-stop":
        sh("docker", "stop", args[0])
    elif action == "docker-restart":
        sh("docker", "restart", args[0])


if __name__ == "__main__":
    main(sys.argv[1:])
