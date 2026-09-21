# SPDX-License-Identifier: Apache-2.0
"""Turn the three #3811 efficacy arms into the tables the finding quotes.

Reads only the run records, the shadow trace and the forward probe of the three
labelled runs, plus the captured position-0 logits rows. Writes four CSVs; no
number in the prose exists that is not in one of them.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib

import numpy as np
from transformers import AutoTokenizer

COLUMNS = {
    "positions": [
        "arm", "turn", "prompt_tokens", "restored_prefix_tokens",
        "selected_tokens", "conversation_tokens", "selected_min", "selected_max",
        "selected_hash", "position_offset", "chunk0_first_position",
        "chunk0_last_position", "chunk0_length", "chunk0_position_span",
        "chunk0_is_dense_run",
    ],
    "logits": [
        "arm", "turn", "vocab", "max_abs_diff_vs_dense", "mean_abs_diff_vs_dense",
        "relative_l2_vs_dense", "argmax_token_id", "dense_argmax_token_id",
        "argmax_agrees_with_dense",
    ],
    "output": [
        "arm", "turn", "output_tokens", "dense_output_tokens",
        "exact_match_dense", "first_differing_token_index",
        "prefix_agreement_ratio", "multiset_agreement_ratio", "output_sha256",
    ],
    "timing": [
        "arm", "turn", "prompt_tokens", "restored_prefix_tokens",
        "foreground_route", "ttft_s", "prefill_s", "e2e_s", "output_tokens",
        "cache_state_matched_across_sparse_arms",
    ],
}


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def turn_of(run_id: str) -> str:
    return run_id.rsplit("/", 1)[-1]


class Arm:
    def __init__(self, d: pathlib.Path, name: str, label: str):
        self.name, self.label = name, label
        self.runs = {turn_of(r["run_id"]): r for r in read_jsonl(d / f"runs-{label}.jsonl")}
        self.texts = {
            turn_of(r["run_id"]): r["text"]
            for r in read_jsonl(d / f"runs-{label}.jsonl.outputs.jsonl")
        }
        probe = read_jsonl(d / f"forward-{label}.jsonl")
        trace = read_jsonl(d / f"trace-{label}.jsonl")
        self.selected = {
            e["request_id"]: e for e in trace
            if e.get("event") == "specprefill.selected"
        }
        self.pre = [e for e in probe if e.get("event") == "specprefill.target.pre_sparse"]
        self.fwd = [e for e in probe if e.get("event") == "adapter.forward"]
        self.positions: dict[str, list[dict]] = {}
        for e in probe:
            if e.get("event") == "model.positions":
                self.positions.setdefault(e["request_id"], []).append(e)
        self.rows = {}
        for i, e in enumerate(self.fwd):
            path = e.get("logits_row")
            if path and pathlib.Path(path).exists():
                self.rows[f"t{i}"] = np.load(path).astype(np.float64)
        self.argmax = {
            f"t{i}": (e.get("logits") or {}).get("argmax") for i, e in enumerate(self.fwd)
        }


def write(path: pathlib.Path, kind: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS[kind], lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in COLUMNS[kind]})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="directory holding the run artifacts")
    ap.add_argument("--labels", required=True, help="JSON {arm: label}")
    ap.add_argument("--tokenizer", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    d = pathlib.Path(args.source)
    labels = json.loads(args.labels)
    arms = {n: Arm(d, n, lab) for n, lab in labels.items()}
    turns = [f"t{i}" for i in range(6)]
    out = pathlib.Path(args.out_dir)

    pos_rows = []
    for name in ("pre", "post"):
        arm = arms[name]
        for i, p in enumerate(arm.pre):
            chunks = sorted(
                arm.positions.get(p["request_id"], []), key=lambda e: e.get("call_index", 0)
            )
            digest = (chunks[0].get("position_ids") or {}) if chunks else {}
            sel = arm.selected.get(p["request_id"], {})
            row = {
                "arm": name, "turn": f"t{i}",
                "prompt_tokens": p["prompt_tokens"],
                "restored_prefix_tokens": p["cached_tokens"],
                "selected_tokens": p["selected_count"],
                "conversation_tokens": p["conversation_token_count"],
                "selected_min": sel.get("selected_min"),
                "selected_max": sel.get("selected_max"),
                "selected_hash": sel.get("selected_hash"),
                "position_offset": p["position_offset"],
            }
            if digest.get("present"):
                length = digest["shape"][-1]
                span = digest["max"] - digest["min"] + 1
                row.update(
                    chunk0_first_position=digest["min"],
                    chunk0_last_position=digest["max"],
                    chunk0_length=length,
                    chunk0_position_span=span,
                    chunk0_is_dense_run=(span == length),
                )
            pos_rows.append(row)
    write(out / "specprefill-position-contract.csv", "positions", pos_rows)

    logit_rows = []
    for t in turns:
        base = arms["dense"].rows.get(t)
        for name in ("pre", "post"):
            row = arms[name].rows.get(t)
            if base is None or row is None or base.shape != row.shape:
                continue
            diff = np.abs(row - base)
            logit_rows.append({
                "arm": name, "turn": t, "vocab": int(row.shape[-1]),
                "max_abs_diff_vs_dense": round(float(diff.max()), 6),
                "mean_abs_diff_vs_dense": round(float(diff.mean()), 8),
                "relative_l2_vs_dense": round(
                    float(np.linalg.norm(row - base) / np.linalg.norm(base)), 8
                ),
                "argmax_token_id": int(row.argmax()),
                "dense_argmax_token_id": int(base.argmax()),
                "argmax_agrees_with_dense": int(row.argmax()) == int(base.argmax()),
            })
    write(out / "specprefill-position-logits.csv", "logits", logit_rows)

    tok = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
    out_rows = []
    for t in turns:
        base = tok.encode(arms["dense"].texts.get(t, ""))
        for name in ("pre", "post"):
            seq = tok.encode(arms[name].texts.get(t, ""))
            first = next((i for i, (x, y) in enumerate(zip(seq, base)) if x != y), None)
            if first is None and len(seq) != len(base):
                first = min(len(seq), len(base))
            prefix = first if first is not None else min(len(seq), len(base))
            denom = max(len(seq), len(base)) or 1
            common = sum(min(seq.count(x), base.count(x)) for x in set(seq))
            correctness = (arms[name].runs.get(t, {}).get("correctness") or {})
            out_rows.append({
                "arm": name, "turn": t,
                "output_tokens": len(seq), "dense_output_tokens": len(base),
                "exact_match_dense": seq == base,
                "first_differing_token_index": first,
                "prefix_agreement_ratio": round(prefix / denom, 6),
                "multiset_agreement_ratio": round(common / denom, 6),
                "output_sha256": correctness.get("output_sha256"),
            })
    write(out / "specprefill-position-output.csv", "output", out_rows)

    # A turn is comparable across the sparse arms only when both restored the
    # same prefix and selected the same set.
    matched = {
        t: (
            arms["pre"].runs.get(t, {}).get("cached_tokens")
            == arms["post"].runs.get(t, {}).get("cached_tokens")
            and {e["selected_hash"] for e in arms["pre"].selected.values()}
            and _hash_for(arms["pre"], t) == _hash_for(arms["post"], t)
        )
        for t in turns
    }
    timing_rows = []
    for t in turns:
        for name in ("dense", "pre", "post"):
            r = arms[name].runs.get(t, {})
            timing_rows.append({
                "arm": name, "turn": t,
                "prompt_tokens": r.get("prompt_tokens"),
                "restored_prefix_tokens": r.get("cached_tokens"),
                "foreground_route": (r.get("raw_usage") or {}).get("foreground_route"),
                "ttft_s": r.get("ttft_s"), "prefill_s": r.get("prefill_s"),
                "e2e_s": r.get("e2e_s"), "output_tokens": r.get("output_tokens"),
                "cache_state_matched_across_sparse_arms": bool(matched.get(t)),
            })
    write(out / "specprefill-position-timing.csv", "timing", timing_rows)
    print(f"wrote 4 CSVs to {out}: {len(pos_rows)} position rows, "
          f"{len(logit_rows)} logit rows, {len(out_rows)} output rows, "
          f"{len(timing_rows)} timing rows")


def _hash_for(arm: Arm, turn: str) -> str | None:
    index = int(turn[1:])
    if index >= len(arm.pre):
        return None
    return (arm.selected.get(arm.pre[index]["request_id"], {}) or {}).get("selected_hash")


if __name__ == "__main__":
    main()
