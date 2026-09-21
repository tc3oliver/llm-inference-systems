"""Run-record construction and validation against schemas/run.schema.json."""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

import jsonschema

SCHEMA_DIR = pathlib.Path(__file__).resolve().parent.parent / "schemas"
RUN_SCHEMA_PATH = SCHEMA_DIR / "run.schema.json"
MANIFEST_SCHEMA_PATH = SCHEMA_DIR / "manifest.schema.yaml"

_cache: dict[str, Any] = {}


def run_schema() -> dict:
    if "run" not in _cache:
        _cache["run"] = json.loads(RUN_SCHEMA_PATH.read_text())
    return _cache["run"]


def manifest_schema() -> dict:
    if "manifest" not in _cache:
        import yaml

        _cache["manifest"] = yaml.safe_load(MANIFEST_SCHEMA_PATH.read_text())
    return _cache["manifest"]


def blank_run() -> dict:
    """A run record with every field present and every metric null."""
    return {
        "run_id": "",
        "exp": "",
        "cell": "",
        "repeat": 0,
        "seed": None,
        "timestamp": "",
        "server_git_sha": None,
        "server_settings_hash": None,
        "model_id": "",
        "model_revision": None,
        "endpoint": "openai",
        "prompt_tokens": None,
        "cached_tokens": None,
        "uncached_suffix_tokens": None,
        "output_tokens": None,
        "ttft_s": None,
        "prefill_s": None,
        "decode_s": None,
        "e2e_s": None,
        "decode_tps": None,
        "spec": {"enabled": None, "threshold": None, "keep_frac": None, "scorer": None},
        "mtp": {
            "enabled": None,
            "draft_tokens": None,
            "cycles": None,
            "accepted": None,
            "drafted": None,
            "accept_rate": None,
            "verify_ms": None,
            "backbone_ms": None,
            "depth_accepted": None,
            "depth_drafted": None,
            "zero_cycles": None,
            "head_ms": None,
            "sample_ms": None,
            "cache_ops_ms": None,
            "backbone_ms_is_equal_share": None,
            "source": None,
        },
        "cache": {
            "hit": None,
            "restore_s": None,
            "checkpoint_tokens": None,
            "evictions": None,
            "cleared_before": None,
        },
        "shadow": {
            "arm": None,
            "budget_pct": None,
            "longest_canonical_prefix_tokens": None,
            "canonical_debt_tokens": None,
            "committed_tokens": None,
            "target_tokens": None,
            "runnable_steps": None,
            "scheduled_steps": None,
            "yielded_steps": None,
            "resumed_steps": None,
            "service_s": None,
            "service_share": None,
            "budget_window_s": None,
            "budget_allowance_s": None,
            "window_service_s": None,
            "budget_windows": None,
            "budget_locked_out_windows": None,
            "budget_overshoot_s": None,
            "idle_wall_s": None,
            "idle_service_share": None,
            "publishes": None,
            "restores": None,
            "chunks": None,
            "enabled": None,
            "processed_tokens": None,
            "scorer_s": None,
            "dense_tail_s": None,
        },
        # The foreground prefill route, as the server recorded it at
        # admission. `dense_break_even_tokens` is the threshold that was in
        # force for the request, so the route can be re-derived from the
        # record without assuming which default applied.
        "route": {
            "route": None,
            "tail_tokens": None,
            "cached_tokens": None,
            "threshold_tokens": None,
            "specprefill_enabled": None,
            "draft_model": None,
        },
        "correctness": {"exact_match": None, "top1_agree": None, "cos_sim": None,
                        "output_sha256": None, "output_token_count": None},
        "server_timing": {
            "ttft_s": None,
            "visible_ttft_s": None,
            "prefill_s": None,
            "generation_s": None,
            "decode_tps": None,
            "prompt_tps": None,
            "total_s": None,
            "model_load_s": None,
        },
        "raw_usage": None,
        "notes": None,
    }


def validate_run(record: dict) -> None:
    """Raise jsonschema.ValidationError if the record does not match the schema."""
    jsonschema.validate(record, run_schema())


def is_valid_run(record: dict) -> bool:
    try:
        validate_run(record)
    except jsonschema.ValidationError:
        return False
    return True


def validate_manifest(manifest: dict) -> None:
    jsonschema.validate(manifest, manifest_schema())


def settings_hash(settings: dict | None) -> str:
    """Stable hash of a settings dict, used to detect a cell's settings changing."""
    payload = json.dumps(settings or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def run_id(exp: str, cell: str, repeat: int, settings_digest: str | None = None) -> str:
    parts = [exp, cell, f"r{repeat}"]
    if settings_digest:
        parts.append(settings_digest[:8])
    return "/".join(parts)
