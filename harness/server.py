"""Start, stop, inspect and configure a local research inference server.

    uv run python -m harness.server preflight
    uv run python -m harness.server start
    uv run python -m harness.server status
    uv run python -m harness.server settings --json settings.json
    uv run python -m harness.server stop

Every location comes from an environment variable or a flag; see
harness/settings.py. The server is bound to loopback by default and this module
refuses to talk to any port but the one it is configured with.

The preflight is a guard against starting a large model beside something else
large: it refuses when free memory is low, when a foreign server process is
already holding a lot of memory, or when the port is busy.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import signal
import socket
import subprocess
import sys
import time

import httpx

from . import settings as settings_mod

GB = 1024 ** 3


# ---------------------------------------------------------------- preflight


def vm_stat_pages() -> tuple[dict[str, int], int]:
    """The vm_stat page counters, lowercased, and the page size in bytes."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}, 0
    page_size = 4096
    first = out.splitlines()[0] if out else ""
    if "page size of" in first:
        page_size = int(first.split("page size of")[1].split("bytes")[0].strip())
    counts: dict[str, int] = {}
    for line in out.splitlines()[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        digits = value.strip().rstrip(".")
        if digits.isdigit():
            counts[key.strip().strip('"').lower()] = int(digits)
    return counts, page_size


def wired_plus_compressor_gb() -> float | None:
    """Memory that cannot be reclaimed under pressure, in GB.

    Wired pages and pages occupied by the compressor are the part of memory a
    new model cannot displace. Active pages are not counted: most of that is
    file-backed page cache which the kernel evicts on demand, so counting it
    would refuse a start that is in fact safe.
    """
    counts, page_size = vm_stat_pages()
    if not counts or not page_size:
        return None
    pages = (
        counts.get("pages wired down", 0)
        + counts.get("pages occupied by compressor", 0)
    )
    return pages * page_size / GB


FREE_PCT_RE = re.compile(r"free percentage:\s*(\d+)\s*%", re.IGNORECASE)


def memory_free_percentage() -> float | None:
    """The system-wide free memory percentage that `memory_pressure` reports."""
    try:
        out = subprocess.run(
            ["memory_pressure"], capture_output=True, text=True, timeout=20
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = FREE_PCT_RE.search(out)
    return float(match.group(1)) if match else None


def foreign_processes(process_name: str, own_pid: int | None) -> list[tuple[int, float, str]]:
    """(pid, rss_gb, comm) for processes named `process_name` other than ours."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid,rss,comm"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, rss_s, comm = parts
        if not pid_s.isdigit() or not rss_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == own_pid:
            continue
        if pathlib.PurePath(comm.strip()).name != process_name:
            continue
        found.append((pid, int(rss_s) * 1024 / GB, comm.strip()))
    return found


def port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def preflight_report(cfg: settings_mod.Settings,
                     own_pid: int | None = None) -> tuple[list[str], dict]:
    """The reasons starting a server would be unsafe, and what was measured.

    An empty reason list means it is safe to start. The gates are:

    - the system-wide free memory percentage reported by `memory_pressure`
      must be at or above `min_free_pct`;
    - wired plus compressor memory must be at or below `max_wired_gb`;
    - no other server process may be resident above `max_foreign_rss_gb`;
    - the configured port must be free.
    """
    reasons: list[str] = []
    metrics: dict = {}

    free_pct = memory_free_percentage()
    metrics["free_pct"] = free_pct
    if free_pct is None:
        reasons.append("could not read the free memory percentage from memory_pressure")
    elif free_pct < cfg.min_free_pct:
        reasons.append(
            f"system-wide free memory {free_pct:.0f}% is below the "
            f"{cfg.min_free_pct:.0f}% floor"
        )

    wired_gb = wired_plus_compressor_gb()
    metrics["wired_plus_compressor_gb"] = wired_gb
    if wired_gb is None:
        reasons.append("could not read wired and compressor memory from vm_stat")
    elif wired_gb > cfg.max_wired_gb:
        reasons.append(
            f"wired plus compressor memory {wired_gb:.1f} GB is above the "
            f"{cfg.max_wired_gb:.0f} GB ceiling"
        )

    foreign = foreign_processes(settings_mod.SERVER_PROCESS_NAME, own_pid)
    metrics["foreign_servers"] = [
        {"pid": pid, "rss_gb": round(rss_gb, 2)} for pid, rss_gb, _ in foreign
    ]
    for pid, rss_gb, comm in foreign:
        if rss_gb > cfg.max_foreign_rss_gb:
            reasons.append(
                f"another {comm} (pid {pid}) holds {rss_gb:.1f} GB, "
                f"above the {cfg.max_foreign_rss_gb:.0f} GB ceiling"
            )

    busy = port_busy(cfg.host, cfg.port)
    metrics["port_busy"] = busy
    if busy:
        reasons.append(f"port {cfg.port} on {cfg.host} is already in use")

    return reasons, metrics


def preflight(cfg: settings_mod.Settings, own_pid: int | None = None) -> list[str]:
    """The reasons starting a server would be unsafe. Empty means safe."""
    return preflight_report(cfg, own_pid=own_pid)[0]


# ------------------------------------------------------------------- state


SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
REDACTED = "<redacted>"


def parse_env_overrides(pairs: list[str] | None) -> dict[str, str]:
    """Turn repeated KEY=VALUE arguments into a mapping."""
    overrides = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--env expects KEY=VALUE, got {pair!r}")
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key:
            raise SystemExit(f"--env expects KEY=VALUE, got {pair!r}")
        overrides[key] = value
    return overrides


def redact_env(overrides: dict[str, str]) -> dict[str, str]:
    """The same mapping with anything that looks like a credential masked.

    Server environment is recorded in the instance file and in every run
    record, both of which are meant to be shareable. A value whose name
    suggests a credential is replaced rather than written down.
    """
    return {
        key: (REDACTED if any(hint in key.upper() for hint in SECRET_HINTS) else value)
        for key, value in overrides.items()
    }


def format_env(overrides: dict[str, str] | None) -> str | None:
    """The redacted overrides as one short string, for a run record's notes."""
    if not overrides:
        return None
    safe = redact_env(overrides)
    return ",".join(f"{key}={safe[key]}" for key in sorted(safe))


# The loopback admin API still expects an API key unless the instance's own
# settings file waives it. The harness never edits a settings file it did not
# create, and never weakens a setting on an existing instance.
BASE_SETTINGS = {
    "version": "1.0",
    "auth": {"skip_api_key_verification": True},
}


def init_base(cfg: settings_mod.Settings) -> dict:
    """Create the base path and its settings.json, if they are not there.

    An existing settings.json is left exactly as it is, and the result says
    whether it already waives API key verification. Nothing here writes a host
    name, an address or a credential.
    """
    if cfg.base_path is None:
        raise SystemExit("missing configuration: OMLX_RESEARCH_BASE")
    base = pathlib.Path(cfg.base_path)
    settings_file = base / "settings.json"
    created = not settings_file.exists()
    base.mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    if cfg.ssd_cache_dir:
        pathlib.Path(cfg.ssd_cache_dir).mkdir(parents=True, exist_ok=True)
    if created:
        settings_file.write_text(json.dumps(BASE_SETTINGS, indent=2) + "\n")
        waived = True
    else:
        try:
            existing = json.loads(settings_file.read_text())
        except (OSError, json.JSONDecodeError):
            existing = {}
        waived = bool((existing.get("auth") or {}).get("skip_api_key_verification"))
    return {
        "base_path": str(base),
        "settings_file": str(settings_file),
        "created": created,
        "skip_api_key_verification": waived,
    }


def read_instance(cfg: settings_mod.Settings) -> dict | None:
    path = cfg.instance_file
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_instance(cfg: settings_mod.Settings, payload: dict) -> None:
    path = cfg.instance_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def server_git_sha(cfg: settings_mod.Settings) -> str | None:
    if not cfg.src or not pathlib.Path(cfg.src).exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(cfg.src), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ----------------------------------------------------------------- health


def wait_for_health(cfg: settings_mod.Settings, timeout_s: float = 300.0,
                    poll_s: float = 1.0, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            resp = httpx.get(f"{cfg.url}/health", headers=cfg.headers(), timeout=5.0)
            if resp.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(poll_s)
    return False


def health(cfg: settings_mod.Settings) -> dict | None:
    try:
        resp = httpx.get(f"{cfg.url}/health", headers=cfg.headers(), timeout=5.0)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return {"status": "ok"}


# --------------------------------------------------------------- lifecycle


def start(cfg: settings_mod.Settings, timeout_s: float = 300.0,
          extra_env: dict[str, str] | None = None) -> dict:
    """Start the server, wait for health, and record the instance file.

    ``extra_env`` is passed into the server environment and recorded in the
    instance file, so a run can prove which overrides the server saw.
    """
    missing = [
        name for name, value in (
            ("OMLX_BIN", cfg.bin),
            ("OMLX_MODEL_DIR", cfg.model_dir),
            ("OMLX_RESEARCH_BASE", cfg.base_path),
        ) if value is None
    ]
    if missing:
        raise SystemExit(f"missing configuration: {', '.join(missing)}")
    if not pathlib.Path(cfg.bin).exists():
        raise SystemExit("server binary not found at the configured OMLX_BIN path")

    existing = read_instance(cfg)
    if existing and pid_alive(existing.get("pid", -1)) and health(cfg):
        return {"already_running": True, **existing}

    reasons = preflight(cfg)
    if reasons:
        for reason in reasons:
            print(f"preflight: {reason}", file=sys.stderr)
        raise SystemExit(2)

    cfg.base_path.mkdir(parents=True, exist_ok=True)
    (cfg.base_path / "logs").mkdir(parents=True, exist_ok=True)
    cfg.ssd_cache_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(cfg.bin), "serve",
        "--model-dir", str(cfg.model_dir),
        "--host", cfg.host,
        "--port", str(cfg.port),
        "--base-path", str(cfg.base_path),
        "--memory-guard-gb", str(int(cfg.memory_guard_gb)),
        "--paged-ssd-cache-dir", str(cfg.ssd_cache_dir),
        "--log-level", "info",
    ]
    env = dict(os.environ, OMLX_BASE_PATH=str(cfg.base_path), **(extra_env or {}))
    stdout_path = cfg.base_path / "logs" / "harness-server.out"
    handle = stdout_path.open("ab")
    proc = subprocess.Popen(
        cmd, stdout=handle, stderr=subprocess.STDOUT, env=env, start_new_session=True
    )

    if not wait_for_health(cfg, timeout_s=timeout_s, proc=proc):
        try:
            proc.terminate()
        except OSError:
            pass
        raise SystemExit(
            f"server did not become healthy within {timeout_s:.0f}s "
            f"(see logs under the configured base path)"
        )

    payload = {
        "pid": proc.pid,
        "port": cfg.port,
        "host": cfg.host,
        "base_path": str(cfg.base_path),
        "model": cfg.model,
        "server_git_sha": server_git_sha(cfg),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "started_by": "harness.server",
        # Redacted: this file is shareable, so a value whose name
        # suggests a credential is not written down.
        "server_env": redact_env(extra_env or {}),
    }
    write_instance(cfg, payload)
    return payload


def stop(cfg: settings_mod.Settings, timeout_s: float = 60.0) -> dict:
    """Stop only the process this harness recorded in the instance file."""
    instance = read_instance(cfg)
    if not instance or "pid" not in instance:
        return {"stopped": False, "reason": "no instance file recorded by this harness"}
    if instance.get("started_by") != "harness.server":
        return {"stopped": False, "reason": "instance was not started by this harness"}
    pid = int(instance["pid"])
    if not pid_alive(pid):
        cfg.instance_file.unlink(missing_ok=True)
        return {"stopped": False, "pid": pid, "reason": "process was not running"}

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            break
        time.sleep(0.5)
    else:
        os.kill(pid, signal.SIGKILL)
        time.sleep(1.0)

    cfg.instance_file.unlink(missing_ok=True)
    return {
        "stopped": not pid_alive(pid),
        "pid": pid,
        "port_free": not port_busy(cfg.host, cfg.port),
    }


def status(cfg: settings_mod.Settings) -> dict:
    instance = read_instance(cfg)
    return {
        "instance": instance,
        "pid_alive": pid_alive(instance["pid"]) if instance and "pid" in instance else False,
        "port_busy": port_busy(cfg.host, cfg.port),
        "health": health(cfg),
        "url": cfg.url,
    }


# ------------------------------------------------------------ admin: model


def _settings_url(cfg: settings_mod.Settings, model: str) -> str:
    return f"{cfg.url}/admin/api/models/{model}/settings"


def apply_settings(cfg: settings_mod.Settings, values: dict,
                   model: str | None = None, timeout_s: float = 120.0) -> dict:
    """PUT per-model settings and return the server's readback."""
    model = model or cfg.model
    if not model:
        raise SystemExit("no model id: set MODEL or pass --model")
    resp = httpx.put(
        _settings_url(cfg, model), json=values, headers=cfg.headers(), timeout=timeout_s
    )
    resp.raise_for_status()
    return get_settings(cfg, model=model)


def get_settings(cfg: settings_mod.Settings, model: str | None = None,
                 timeout_s: float = 60.0) -> dict:
    """Read a model's settings back from the admin models listing.

    The per-model settings route accepts PUT only; the listing carries every
    model's effective settings under ``settings``.
    """
    model = model or cfg.model
    resp = httpx.get(f"{cfg.url}/admin/api/models", headers=cfg.headers(), timeout=timeout_s)
    resp.raise_for_status()
    body = resp.json()
    models = body.get("models", body) if isinstance(body, dict) else body
    for entry in models or []:
        if isinstance(entry, dict) and entry.get("id") == model:
            found = entry.get("settings")
            return found if isinstance(found, dict) else entry
    raise SystemExit(f"model {model!r} not present in the admin models listing")


def load_model(cfg: settings_mod.Settings, model: str | None = None,
               timeout_s: float = 900.0) -> int:
    model = model or cfg.model
    resp = httpx.post(
        f"{cfg.url}/admin/api/models/{model}/load", headers=cfg.headers(), timeout=timeout_s
    )
    return resp.status_code


def unload_model(cfg: settings_mod.Settings, model: str | None = None,
                 timeout_s: float = 300.0) -> int:
    model = model or cfg.model
    resp = httpx.post(
        f"{cfg.url}/admin/api/models/{model}/unload", headers=cfg.headers(), timeout=timeout_s
    )
    return resp.status_code


CACHE_CLEAR_PATHS = ("/admin/api/hot-cache/clear", "/admin/api/ssd-cache/clear")


def clear_caches(cfg: settings_mod.Settings, timeout_s: float = 120.0) -> dict:
    """Clear the research instance's hot and SSD prefix caches.

    This only ever touches the host and port this harness is configured with,
    which is the research instance, never a long-running server elsewhere.
    """
    results = {}
    for path in CACHE_CLEAR_PATHS:
        try:
            resp = httpx.post(f"{cfg.url}{path}", headers=cfg.headers(), timeout=timeout_s)
            results[path] = resp.status_code
        except httpx.HTTPError as error:
            results[path] = f"error: {type(error).__name__}"
    return results


def reload_model(cfg: settings_mod.Settings, model: str | None = None) -> dict:
    """Unload then load, so a load-time setting takes effect."""
    unloaded = unload_model(cfg, model=model)
    loaded = load_model(cfg, model=model)
    return {"unload_status": unloaded, "load_status": loaded}


# --------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness.server", description=__doc__)
    parser.add_argument("command",
                        choices=["preflight", "start", "stop", "status", "settings",
                                 "load", "unload", "reload", "clear-caches",
                                 "init-base"])
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-path", default=None,
                        help="server base path; defaults to OMLX_RESEARCH_BASE")
    parser.add_argument("--json", dest="json_file", default=None,
                        help="settings: a JSON file of per-model settings to apply")
    parser.add_argument("--env", action="append", dest="env", default=None,
                        metavar="KEY=VALUE",
                        help="start: an environment variable for the server "
                             "process; repeatable. Recorded in the instance "
                             "file and in every run's notes, with anything "
                             "that looks like a credential redacted.")
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = settings_mod.load(
        host=args.host,
        port=args.port,
        model=args.model,
        base_path=pathlib.Path(args.base_path).expanduser() if args.base_path else None,
    )

    if args.command == "init-base":
        result = init_base(cfg)
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["skip_api_key_verification"]:
            print("init-base: the existing settings.json does not set "
                  "auth.skip_api_key_verification; the admin API will require a "
                  "key, so set OMLX_API_KEY or edit that file yourself",
                  file=sys.stderr)
            return 1
        return 0

    if args.command == "preflight":
        reasons, metrics = preflight_report(cfg)
        print(json.dumps(metrics, indent=2, sort_keys=True))
        if reasons:
            for reason in reasons:
                print(f"preflight: {reason}", file=sys.stderr)
            return 2
        print("preflight: ok")
        return 0

    if args.command == "start":
        extra = parse_env_overrides(args.env)
        print(json.dumps(start(cfg, timeout_s=args.timeout, extra_env=extra),
                         indent=2, sort_keys=True))
        return 0

    if args.command == "stop":
        result = stop(cfg)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("stopped") or result.get("reason") else 1

    if args.command == "status":
        print(json.dumps(status(cfg), indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "settings":
        if args.json_file:
            values = json.loads(pathlib.Path(args.json_file).read_text())
            readback = apply_settings(cfg, values, timeout_s=args.timeout)
        else:
            readback = get_settings(cfg)
        print(json.dumps(readback, indent=2, sort_keys=True))
        return 0

    if args.command == "load":
        print(json.dumps({"status": load_model(cfg)}))
        return 0
    if args.command == "unload":
        print(json.dumps({"status": unload_model(cfg)}))
        return 0
    if args.command == "clear-caches":
        print(json.dumps(clear_caches(cfg), indent=2, sort_keys=True))
        return 0
    if args.command == "reload":
        print(json.dumps(reload_model(cfg), indent=2, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
