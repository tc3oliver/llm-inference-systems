"""Run an experiment grid and append one validated JSON line per measured run.

    uv run python -m harness.run --config experiments/exp-00N/config.yaml \
        --out experiments/exp-00N/data/raw/runs.jsonl
    uv run python -m harness.run --config ... --out ... --dry-run

The config lists cells. Settings are applied once per cell and re-applied only
when the settings hash changes; with `reload_on_change: true` (the default) the
model is unloaded and loaded again whenever the settings change, so a load-time
setting such as multi-token prediction or KV quantization takes effect.

The runner is resumable: a run_id already present in the output file is skipped.
Only token counts, timings and server-reported usage are written. Prompt text is
never written to the output file.

Config shape:

    exp: exp-002
    model: <model id>            # optional; falls back to the MODEL env var
    reload_on_change: true
    defaults:
      endpoint: openai
      repeats: 1
      max_tokens: 128
      seed: 0
    cells:
      - name: mtp-off
        settings: {mtp_enabled: false}
        request: {max_tokens: 64, endpoint: openai, extra_body: {}}
        workload: {prompt_tokens: 512, kind: code, seed: 1}
        repeats: 3
      - name: session
        settings: {mtp_enabled: true, mtp_num_draft_tokens: 3}
        workload: {shape: workloads/shapes/read.yaml, seed: 2}
        repeats: 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from concurrent import futures
from typing import Any, Callable

import yaml

from . import client as client_mod
from . import mtp_log
from . import schema
from . import server as server_mod
from . import settings as settings_mod

ClientFn = Callable[..., dict]


# ------------------------------------------------------------------- config


def load_config(path: str | pathlib.Path) -> dict:
    config = yaml.safe_load(pathlib.Path(path).read_text())
    if not isinstance(config, dict) or not config.get("cells"):
        raise SystemExit("config must be a mapping with a non-empty `cells` list")
    config.setdefault("defaults", {})
    config.setdefault("reload_on_change", True)
    return config


def _merged(cell: dict, defaults: dict, key: str, fallback: Any = None) -> Any:
    request = cell.get("request") or {}
    if key in request:
        return request[key]
    if key in cell:
        return cell[key]
    if key in defaults:
        return defaults[key]
    return fallback


# ---------------------------------------------------------------- resumable


def existing_run_ids(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    seen = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line)["run_id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def append_run(path: pathlib.Path, record: dict) -> None:
    schema.validate_run(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
        fh.flush()


# ----------------------------------------------------------------- planning


def _turns_for_cell(cell: dict, defaults: dict, model_dir=None,
                    count_fn=None) -> list[dict]:
    """Expand a cell's workload into the turns of one repeat."""
    from workloads import generator

    workload = cell.get("workload") or {}
    # A task instruction appended to every user turn decides what the model
    # is asked to produce; the generated text before it only sets the prompt
    # geometry. Without one the model is answering a bare dump of text.
    instruction = workload.get("instruction", defaults.get("instruction"))
    if workload.get("shape"):
        shape_path = pathlib.Path(workload["shape"])
        turns = generator.make_session(
            shape_path, seed=int(workload.get("seed", defaults.get("seed", 0))),
            model_dir=model_dir, count_fn=count_fn,
        )
        if instruction:
            for turn in turns:
                turn["text"] = turn["text"] + "\n\n" + instruction
        # Idle between turns is part of a shape's geometry, but a faithful
        # 300 s pause costs 300 s of wall clock per repeat. A scale factor
        # keeps the ordering of the gaps while bounding the run, and every
        # record says which factor produced it.
        idle_scale = workload.get("idle_scale", defaults.get("idle_scale"))
        if idle_scale is not None:
            for turn in turns:
                turn["idle_s"] = float(turn["idle_s"]) * float(idle_scale)
                turn["idle_scale"] = float(idle_scale)
        return turns
    tokens = int(workload.get("prompt_tokens", defaults.get("prompt_tokens", 512)))
    kind = workload.get("kind", defaults.get("kind", "prose"))
    seed = int(workload.get("seed", defaults.get("seed", 0)))
    text, info = generator.make_prompt(
        tokens, kind=kind, seed=seed, model_dir=model_dir, count_fn=count_fn
    )
    if instruction:
        text = text + "\n\n" + instruction
    return [{
        "index": 0,
        "text": text,
        "add_tokens": tokens,
        "actual_tokens": info["actual_tokens"],
        "kind": kind,
        "output_max_tokens": int(_merged(cell, defaults, "max_tokens", 128)),
        "idle_s": 0.0,
        "approx": info["approx"],
    }]


def plan(config: dict, out_path: pathlib.Path) -> list[dict]:
    """The runs this config would produce, minus the ones already recorded."""
    defaults = config["defaults"]
    done = existing_run_ids(out_path)
    entries = []
    for cell in config["cells"]:
        digest = schema.settings_hash(cell.get("settings"))
        repeats = int(_merged(cell, defaults, "repeats", 1))
        turn_count = 1
        workload = cell.get("workload") or {}
        if workload.get("shape"):
            shape = yaml.safe_load(pathlib.Path(workload["shape"]).read_text())
            turn_count = len(shape.get("turns") or [])
        concurrency = max(1, int(_merged(cell, defaults, "concurrency", 1)))
        for repeat in range(repeats):
            for turn in range(turn_count):
                for slot in range(concurrency):
                    rid = run_identifier(config["exp"], cell["name"], repeat, turn,
                                         turn_count, digest, slot, concurrency)
                    entries.append({
                        "run_id": rid,
                        "cell": cell["name"],
                        "repeat": repeat,
                        "turn": turn,
                        "slot": slot,
                        "concurrency": concurrency,
                        "settings_hash": digest,
                        "skip": rid in done,
                    })
    return entries


def run_identifier(exp: str, cell: str, repeat: int, turn: int, turn_count: int,
                   digest: str, slot: int = 0, concurrency: int = 1) -> str:
    rid = schema.run_id(exp, cell, repeat, digest)
    if turn_count > 1:
        rid = f"{rid}/t{turn}"
    if concurrency > 1:
        rid = f"{rid}/c{slot}"
    return rid


# ------------------------------------------------------------------ execute


def _request_overrides(cell: dict, defaults: dict) -> dict:
    """Request-level sampling overrides folded into the request body."""
    body = dict(_merged(cell, defaults, "extra_body", None) or {})
    for key in ("temperature", "seed", "top_p", "top_k"):
        value = _merged(cell, defaults, key, None)
        if value is not None:
            body[key] = value
    return body or None


def _sidecar_path(out_path: pathlib.Path) -> pathlib.Path:
    return out_path.with_name(out_path.name + ".outputs.jsonl")


def _append_output(out_path: pathlib.Path, run_id: str, cell: str, repeat: int,
                   text: str) -> None:
    """Append a completion to the sidecar file.

    This is the model's answer to a synthetic prompt. The prompt is not written
    here or anywhere else.
    """
    path = _sidecar_path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps({"run_id": run_id, "cell": cell, "repeat": repeat,
                             "text": text}, sort_keys=True) + "\n")
        fh.flush()


def _fill_mtp(record: dict, cell_settings: dict, observed, from_usage: dict,
              ambiguous: bool) -> None:
    """Populate the mtp block, preferring the server's usage counters.

    The log line and the usage object can both be present. Usage wins field by
    field, because it belongs to the request that reported it, while a log line
    is matched only by position and time window.
    """
    record["mtp"]["enabled"] = cell_settings.get("mtp_enabled")
    record["mtp"]["draft_tokens"] = cell_settings.get("mtp_num_draft_tokens")
    sources = []
    if observed is not None and not ambiguous:
        record["mtp"].update(observed.as_run_fields(
            enabled=record["mtp"]["enabled"],
            draft_tokens=record["mtp"]["draft_tokens"],
        ))
        sources.append("log")
    if from_usage:
        for key, value in from_usage.items():
            if value is not None:
                record["mtp"][key] = value
        sources.append("usage")
    record["mtp"]["source"] = "+".join(reversed(sources)) if sources else None


def execute(config: dict, out_path: pathlib.Path,
            cfg: settings_mod.Settings | None = None,
            client_fn: ClientFn | None = None,
            apply_settings_fn: Callable[[dict], dict] | None = None,
            reload_fn: Callable[[], Any] | None = None,
            clear_caches_fn: Callable[[], Any] | None = None,
            server_sha: str | None = None,
            server_env: dict | None = None,
            log_tail: mtp_log.MTPLogTail | None = None,
            sleep_fn: Callable[[float], None] = time.sleep) -> list[dict]:
    """Run the grid. Injectable callables let tests drive this without a server."""
    cfg = cfg or settings_mod.load()
    client_fn = client_fn or (lambda **kw: client_mod.run_chat(cfg=cfg, **kw))
    defaults = config["defaults"]
    model = config.get("model") or cfg.model or "unknown"
    reload_on_change = bool(config.get("reload_on_change", True))
    if apply_settings_fn is None:
        def apply_settings_fn(values: dict) -> dict:
            return server_mod.apply_settings(cfg, values, model=model)
    if reload_fn is None:
        def reload_fn():
            return server_mod.reload_model(cfg, model=model)
    if clear_caches_fn is None:
        def clear_caches_fn():
            return server_mod.clear_caches(cfg)
    if log_tail is None:
        log_tail = mtp_log.MTPLogTail(cfg.server_log)
    if server_env is None:
        instance = server_mod.read_instance(cfg) or {}
        server_env = instance.get("server_env") or {}
    server_env_note = server_mod.format_env(server_env)

    done = existing_run_ids(out_path)
    written: list[dict] = []
    applied_hash: str | None = None

    for cell in config["cells"]:
        cell_settings = cell.get("settings") or {}
        digest = schema.settings_hash(cell_settings)
        repeats = int(_merged(cell, defaults, "repeats", 1))
        endpoint = _merged(cell, defaults, "endpoint", "openai")
        system = _merged(cell, defaults, "system", None)
        extra_body = _request_overrides(cell, defaults)
        concurrency = max(1, int(_merged(cell, defaults, "concurrency", 1)))
        warmup = max(0, int(_merged(cell, defaults, "warmup", 0)))
        cache_clear = bool(_merged(cell, defaults, "cache_clear", False))
        record_output = bool(_merged(cell, defaults, "record_output", False))

        turns = _turns_for_cell(cell, defaults, model_dir=cfg.model_dir)
        turn_count = len(turns)
        cell_ids = [
            run_identifier(config["exp"], cell["name"], repeat, turn, turn_count,
                           digest, slot, concurrency)
            for repeat in range(repeats)
            for turn in range(turn_count)
            for slot in range(concurrency)
        ]
        if all(rid in done for rid in cell_ids):
            continue

        if cell_settings and digest != applied_hash:
            if applied_hash is not None and reload_on_change:
                apply_settings_fn(cell_settings)
                reload_fn()
            else:
                apply_settings_fn(cell_settings)
            applied_hash = digest

        # Warm-up requests are not counted and are never written out. They let
        # the runtime settle before the first measured repeat.
        for _ in range(warmup):
            client_fn(
                messages=([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": turns[0]["text"]}],
                max_tokens=int(turns[0]["output_max_tokens"]),
                endpoint=endpoint, stream=True, extra_body=extra_body,
                capture_text=False,
            )

        for repeat in range(repeats):
            messages: list[dict] = []
            if system:
                messages.append({"role": "system", "content": system})
            for turn in turns:
                messages.append({"role": "user", "content": turn["text"]})
                slots = [
                    slot for slot in range(concurrency)
                    if run_identifier(config["exp"], cell["name"], repeat,
                                      turn["index"], turn_count, digest, slot,
                                      concurrency) not in done
                ]
                if not slots:
                    messages.append({"role": "assistant", "content": ""})
                    continue
                if turn["idle_s"]:
                    sleep_fn(turn["idle_s"])

                if cache_clear:
                    clear_caches_fn()

                log_tail.mark()
                started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                payload = dict(
                    messages=list(messages),
                    max_tokens=int(turn["output_max_tokens"]),
                    endpoint=endpoint,
                    stream=True,
                    extra_body=extra_body,
                    capture_text=record_output,
                )
                group_started = time.monotonic()
                if concurrency == 1:
                    results = {slots[0]: client_fn(**payload)}
                else:
                    with futures.ThreadPoolExecutor(max_workers=len(slots)) as pool:
                        submitted = {
                            pool.submit(client_fn, **payload): slot for slot in slots
                        }
                        results = {
                            slot: future.result()
                            for future, slot in submitted.items()
                        }
                group_wall_s = time.monotonic() - group_started

                observed_lines = log_tail.collect() if log_tail else []
                messages.append({"role": "assistant", "content": ""})

                for position, slot in enumerate(sorted(results)):
                    result = results[slot]
                    rid = run_identifier(config["exp"], cell["name"], repeat,
                                         turn["index"], turn_count, digest, slot,
                                         concurrency)
                    record = schema.blank_run()
                    record.update({
                        "run_id": rid,
                        "exp": config["exp"],
                        "cell": cell["name"],
                        "repeat": repeat,
                        "seed": int((cell.get("workload") or {}).get(
                            "seed", defaults.get("seed", 0))),
                        "timestamp": started,
                        "server_git_sha": server_sha,
                        "server_settings_hash": digest,
                        "model_id": model,
                        "model_revision": config.get("model_revision"),
                        "endpoint": endpoint,
                    })
                    for key in ("prompt_tokens", "cached_tokens",
                                "uncached_suffix_tokens", "output_tokens", "ttft_s",
                                "prefill_s", "decode_s", "e2e_s", "decode_tps",
                                "raw_usage", "server_timing"):
                        if key in result:
                            record[key] = result[key]

                    observed = (observed_lines[position]
                                if position < len(observed_lines) else None)
                    _fill_mtp(record, cell_settings, observed,
                              result.get("mtp_from_usage") or {},
                              ambiguous=concurrency > 1)
                    record["spec"]["enabled"] = cell_settings.get("specprefill_enabled")
                    if cache_clear:
                        record["cache"]["cleared_before"] = True

                    text = result.get("output_text")
                    if record_output and text is not None:
                        record["correctness"]["output_sha256"] = hashlib.sha256(
                            text.encode()).hexdigest()
                        record["correctness"]["output_token_count"] = \
                            record["output_tokens"]
                        _append_output(out_path, rid, cell["name"], repeat, text)

                    notes = []
                    if turn.get("approx"):
                        notes.append("prompt length approximated at 4 chars/token")
                    if turn_count > 1:
                        notes.append(f"turn {turn['index']} of {turn_count}")
                    if concurrency > 1:
                        notes.append(f"concurrency={concurrency}")
                        notes.append(f"slot={slot}")
                        notes.append("log-parsed acceptance not attributable per "
                                     "request at concurrency > 1")
                    if result.get("includes_model_load"):
                        # The model was loaded during this request, so the
                        # client-measured ttft carries the load time too.
                        notes.append("includes model load")
                    notes.append(f"repeat={repeat}")
                    if turn.get("idle_scale") is not None:
                        notes.append(f"idle_scale={turn['idle_scale']:g}")
                    if server_env_note:
                        notes.append(f"server_env={server_env_note}")
                    notes.append(f"group_wall_s={group_wall_s:.4f}")
                    record["notes"] = "; ".join(notes) or None

                    append_run(out_path, record)
                    done.add(rid)
                    written.append(record)

    return written


# --------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness.run", description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    out_path = pathlib.Path(args.out)

    if args.dry_run:
        entries = plan(config, out_path)
        pending = [e for e in entries if not e["skip"]]
        for entry in entries:
            mark = "skip" if entry["skip"] else "run "
            print(f"{mark}  {entry['run_id']}")
        print(f"\n{len(pending)} to run, {len(entries) - len(pending)} already recorded")
        return 0

    cfg = settings_mod.load(model=config.get("model"))
    sha = server_mod.server_git_sha(cfg)
    instance = server_mod.read_instance(cfg) or {}
    env_seen = dict(instance.get("server_env") or {})
    env_wanted = dict(config.get("server_env") or {})
    if env_wanted and env_seen != env_wanted:
        print(f"config expects server_env {env_wanted} but the running instance "
              f"recorded {env_seen}; start the server with matching --env", file=sys.stderr)
        return 2
    written = execute(config, out_path, cfg=cfg, server_sha=sha, server_env=env_seen)
    print(f"wrote {len(written)} run(s) to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
