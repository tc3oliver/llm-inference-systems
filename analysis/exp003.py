"""EXP-003 — turn the four-arm session records into the tables the study reads.

    uv run python -m analysis.exp003 RUNS.json

Reads the runner's JSON, writes `data/exp-003/session-turns.csv` and
`data/exp-003/arm-summary.csv`. Nothing is smoothed and nothing is filled: a
quantity the run did not report stays empty.

Two columns need their definitions stated rather than inferred.

`canonical_debt_tokens` is `prompt_tokens - longest_committed_canonical_prefix`,
and the prefix is read from the arm's **dense probe**, not from its turns. A
SpecPrefill turn reports `cached_tokens` of 0 whatever the cache holds, so a
debt computed from the sparse turns would be the prompt length in every arm and
would say nothing about any of them.

`cumulative_foreground_s` sums the turns only. The probe is a measurement
instrument, not part of the session, and its latency is reported beside the
total rather than inside it.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

OUT_DIR = pathlib.Path("data/exp-003")


def _outputs(prefix: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Table paths for a named round. The default round keeps the original
    names, because those paths are already referenced elsewhere."""
    if not prefix:
        return OUT_DIR / "session-turns.csv", OUT_DIR / "arm-summary.csv"
    return OUT_DIR / f"{prefix}-turns.csv", OUT_DIR / f"{prefix}-summary.csv"

TURN_FIELDS = (
    "arm", "kind", "turn", "prompt_tokens", "cached_tokens", "uncached_suffix",
    "ttft_s", "prefill_s", "decode_s", "decode_tps", "output_tokens", "wall_s",
    "output_sha", "shadow_committed_tokens", "shadow_target_tokens",
    "shadow_service_s", "shadow_service_share", "shadow_runnable_steps",
    "shadow_scheduled_steps", "shadow_yielded_steps", "shadow_publishes",
    "shadow_chunks", "shadow_canonical_debt_tokens",
)
SUMMARY_FIELDS = (
    "arm", "cumulative_foreground_s", "probe_ttft_s",
    "longest_canonical_prefix_tokens", "canonical_debt_tokens",
    "final_prompt_tokens", "shadow_service_s", "shadow_publishes",
)


def _shadow(row: dict, key: str):
    value = (row.get("shadow") or {}).get(key)
    return "" if value is None else value


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    data = json.loads(pathlib.Path(argv[1]).read_text())
    turns_out, summary_out = _outputs(argv[2] if len(argv) > 2 else "")
    arms = {k: v for k, v in data.items() if k != "meta"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with turns_out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TURN_FIELDS, lineterminator="\n")
        writer.writeheader()
        for arm, payload in arms.items():
            for row in payload["turns"]:
                writer.writerow({
                    "arm": arm,
                    "kind": row.get("kind"),
                    "turn": row.get("turn"),
                    "prompt_tokens": row.get("prompt_tokens"),
                    "cached_tokens": row.get("cached_tokens"),
                    "uncached_suffix": row.get("uncached_suffix"),
                    "ttft_s": row.get("ttft_s"),
                    "prefill_s": row.get("prefill_s") if row.get("prefill_s") is not None else "",
                    "decode_s": row.get("decode_s"),
                    "decode_tps": row.get("decode_tps") if row.get("decode_tps") is not None else "",
                    "output_tokens": row.get("output_tokens"),
                    "wall_s": row.get("wall_s"),
                    "output_sha": row.get("output_sha"),
                    "shadow_committed_tokens": _shadow(row, "committed_tokens"),
                    "shadow_target_tokens": _shadow(row, "target_tokens"),
                    "shadow_service_s": _shadow(row, "service_s"),
                    "shadow_service_share": _shadow(row, "service_share"),
                    "shadow_runnable_steps": _shadow(row, "runnable_steps"),
                    "shadow_scheduled_steps": _shadow(row, "scheduled_steps"),
                    "shadow_yielded_steps": _shadow(row, "yielded_steps"),
                    "shadow_publishes": _shadow(row, "publishes"),
                    "shadow_chunks": _shadow(row, "chunks"),
                    "shadow_canonical_debt_tokens": _shadow(row, "canonical_debt_tokens"),
                })

    with summary_out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
        writer.writeheader()
        for arm, payload in arms.items():
            probe = next(
                (r for r in payload["turns"] if r.get("kind") == "dense-probe"), None
            )
            writer.writerow({
                "arm": arm,
                "cumulative_foreground_s": payload.get("cumulative_foreground_s"),
                "probe_ttft_s": probe.get("ttft_s") if probe else "",
                "longest_canonical_prefix_tokens": payload.get(
                    "longest_canonical_prefix_tokens", ""
                ),
                "canonical_debt_tokens": payload.get("canonical_debt_tokens", ""),
                "final_prompt_tokens": probe.get("prompt_tokens") if probe else "",
                "shadow_service_s": _shadow(probe, "service_s") if probe else "",
                "shadow_publishes": _shadow(probe, "publishes") if probe else "",
            })

    print(f"wrote {turns_out} and {summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
