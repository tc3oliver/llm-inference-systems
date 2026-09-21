# SPDX-License-Identifier: Apache-2.0
"""The three-arm semantic control, as one table.

Each arm runs the same six-turn session at the same seed. The sixth turn is a
`repeat`: it re-sends the fifth turn's prompt unchanged, so the probe reaches
the server as the identical token sequence in every arm and the only thing
differing is what the cache had already stored for it.

Two comparisons come out of that, and they are not the same question.

*Across arms*, on the probe: does a request served from recovered canonical
state answer the way the dense reference answers, the way an unrecovered
sparse request answers, or neither?

*Within an arm*, between turn 4 and the probe: the two send the same prompt, so
an arm that answers them differently is not reproducing itself. That one is
free — it needs no extra run — and it is the stricter test, because an arm is
being compared against itself under the conditions it just created.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib

COLUMNS = [
    "arm", "turn", "prompt_tokens", "restored_prefix_tokens",
    "uncached_tail_tokens", "foreground_route", "canonical_committed_tokens",
    "output_sha256", "ttft_s",
]

SUMMARY_COLUMNS = [
    "arm", "probe_restored_prefix_tokens", "probe_route", "probe_output_sha256",
    "matches_dense_reference", "repeat_is_reproducible",
]


def read_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _turn(record: dict) -> str:
    return record["run_id"].rsplit("/", 1)[-1]


def rows_for(arm: str, records: list[dict]) -> list[dict]:
    rows = []
    for record in sorted(records, key=lambda r: _turn(r)):
        rows.append({
            "arm": arm,
            "turn": _turn(record),
            "prompt_tokens": record.get("prompt_tokens"),
            "restored_prefix_tokens": record.get("cached_tokens"),
            "uncached_tail_tokens": record.get("uncached_suffix_tokens"),
            "foreground_route": (record.get("route") or {}).get("route"),
            "canonical_committed_tokens": (record.get("shadow") or {}).get(
                "committed_tokens"
            ),
            "output_sha256": (record.get("correctness") or {}).get("output_sha256"),
            "ttft_s": record.get("ttft_s"),
        })
    return rows


def write_csv(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row[k]) for k in columns})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-runs", required=True)
    parser.add_argument("--spec-runs", required=True)
    parser.add_argument("--recovery-runs", required=True)
    parser.add_argument("--out-dir", default="data/pcsr-agent-validation")
    args = parser.parse_args()

    arms = {
        "dense": rows_for("dense", read_jsonl(pathlib.Path(args.dense_runs))),
        "spec": rows_for("spec", read_jsonl(pathlib.Path(args.spec_runs))),
        "recovery": rows_for("recovery", read_jsonl(pathlib.Path(args.recovery_runs))),
    }
    every = [row for rows in arms.values() for row in rows]

    def probe(rows: list[dict]) -> dict:
        return next(row for row in rows if row["turn"] == "t5")

    def fourth(rows: list[dict]) -> dict:
        return next(row for row in rows if row["turn"] == "t4")

    dense_sha = probe(arms["dense"])["output_sha256"]
    summary = []
    for arm, rows in arms.items():
        p, f = probe(rows), fourth(rows)
        summary.append({
            "arm": arm,
            "probe_restored_prefix_tokens": p["restored_prefix_tokens"],
            "probe_route": p["foreground_route"],
            "probe_output_sha256": p["output_sha256"],
            "matches_dense_reference": p["output_sha256"] == dense_sha,
            "repeat_is_reproducible": p["output_sha256"] == f["output_sha256"],
        })

    out = pathlib.Path(args.out_dir)
    write_csv(out / "semantic-control-turns.csv", COLUMNS, every)
    write_csv(out / "semantic-control-summary.csv", SUMMARY_COLUMNS, summary)
    print(f"wrote {len(every)} turn rows and {len(summary)} summary rows to {out}")


if __name__ == "__main__":
    main()
