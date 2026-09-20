"""Flatten the EXP-002 run records into `data/exp-002/runs.csv`.

    uv run python -m analysis.exp002_flatten

Reads every `data/exp-002/raw/*.jsonl` — the primary artifact, one validated
record per measured run as `harness/run.py` wrote it — and emits the flat table
the analysis and the figures read. Derived columns are computed here and named
in `data/exp-002/README.md`; nothing is renamed and nothing is dropped that a
comparison depends on.
"""

from __future__ import annotations

import csv
import json
import pathlib

RAW = pathlib.Path("data/exp-002/raw")
OUT = pathlib.Path("data/exp-002/runs.csv")

# Cell names encode policy/content/context, and the production-model cells carry
# a fourth segment naming the model. The default is the model the rest of the
# matrix ran on.
POLICIES = {"dense": "dense", "densectl": "dense", "fixed3": "fixed-3",
            "adaptive": "adaptive"}
DEFAULT_MODEL = "Qwen3.6-35B-A3B-oQ6"
MODELS = {"27b": "Qwen3.8-27B-oQ4e"}


def _cell(name: str) -> tuple[str, str, str, str]:
    parts = name.split("/")
    model = MODELS.get(parts[3]) if len(parts) > 3 else None
    return model or DEFAULT_MODEL, POLICIES[parts[0]], parts[1], parts[2]


def _round(value, digits=4):
    return round(value, digits) if value is not None else None


def flatten(record: dict) -> dict:
    model, policy, content, context = _cell(record["cell"])
    mtp = record["mtp"]
    timing = record["server_timing"] or {}
    cycles, accepted = mtp.get("cycles"), mtp.get("accepted")
    output = record["output_tokens"]

    # The client measures decode from the first content delta to the last. One
    # model's stream carried no incremental deltas at all, so that interval is
    # zero and the server's own generation duration is used instead. Which one
    # produced the number is recorded, and the two are never mixed inside a
    # comparison.
    client = record["decode_s"] or None
    decode = client or timing.get("generation_s")

    spec_tokens = accepted + cycles if (accepted is not None and cycles) else None
    component_ms = sum(
        mtp.get(key) or 0
        for key in ("backbone_ms", "head_ms", "sample_ms", "cache_ops_ms")
    ) if cycles else None

    return {
        "run_id": record["run_id"], "model": model, "policy": policy,
        "content": content, "context": context, "repeat": record["repeat"],
        "prompt_tokens": record["prompt_tokens"], "output_tokens": output,
        "ttft_s": _round(record["ttft_s"]), "decode_s": _round(decode),
        "decode_time_source": ("client" if client
                               else "server" if decode else None),
        "e2e_s": _round(record["e2e_s"]),
        "decode_ms_per_token": _round(decode * 1000 / (output - 1))
        if decode and output and output > 1 else None,
        "cycles": cycles, "accepted": accepted, "drafted": mtp.get("drafted"),
        "accept_rate": mtp.get("accept_rate"),
        "spec_tokens": spec_tokens,
        "spec_share": _round(spec_tokens / output) if spec_tokens else None,
        "tok_per_cycle": _round(spec_tokens / cycles) if spec_tokens else None,
        "zero_cycles": mtp.get("zero_cycles"),
        "backbone_ms": mtp.get("backbone_ms"), "head_ms": mtp.get("head_ms"),
        "sample_ms": mtp.get("sample_ms"), "cache_ops_ms": mtp.get("cache_ops_ms"),
        "backbone_ms_per_cycle": _round(mtp["backbone_ms"] / cycles)
        if mtp.get("backbone_ms") and cycles else None,
        "spec_ms_per_cycle": _round(component_ms / cycles)
        if component_ms and cycles else None,
        "depth_drafted": "|".join(map(str, mtp["depth_drafted"]))
        if mtp.get("depth_drafted") else None,
        "depth_accepted": "|".join(map(str, mtp["depth_accepted"]))
        if mtp.get("depth_accepted") else None,
        "output_sha256": record["correctness"]["output_sha256"],
        "mtp_source": mtp.get("source"),
        "server_git_sha": record["server_git_sha"],
        "settings_hash": record["server_settings_hash"],
        "notes": record["notes"],
    }


def main() -> None:
    rows = []
    for path in sorted(RAW.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(flatten(json.loads(line)))
    rows.sort(key=lambda r: (r["model"], r["content"], r["context"],
                             r["policy"], r["repeat"]))
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} runs -> {OUT}")


if __name__ == "__main__":
    main()
