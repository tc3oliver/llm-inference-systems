# SPDX-License-Identifier: Apache-2.0
"""Derived tables for the zero-idle validation round attached to EXP-003.

Reads the runner's JSON records for the two arms and, for the PCSR arm, the
runtime's own shadow trace. Writes the two CSVs under
``data/pcsr-agent-validation/``. Nothing here smooths, interpolates or
back-generates: a quantity a run did not report is an empty cell.

The trace is the reason this file exists rather than a summary by hand. The
question the round has to answer — did a later foreground request restore a
boundary the background job published — cannot be read off a latency, and the
usage counters report totals rather than which publication a restore consumed.
The trace carries both ends: ``shadow.publish`` records the boundary, and
``foreground.fetch`` records how many tokens the next request matched.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib

TURN_COLUMNS = [
    "arm", "turn", "prompt_tokens", "cached_tokens", "uncached_tail_tokens",
    "foreground_route", "ttft_s", "prefill_s", "e2e_s", "output_tokens",
    "output_sha", "context_growth_tokens", "recovery_service_s",
    "recovery_processed_tokens", "canonical_committed_tokens",
    "canonical_progress_tokens", "canonical_debt_tokens", "publications",
    "recovery_between_turns_tokens",
]

SUMMARY_COLUMNS = [
    "arm", "turns", "final_prompt_tokens", "cumulative_foreground_s",
    "total_recovery_service_s", "total_recovered_tokens", "total_publications",
    "turns_restoring_published_state", "growth_vs_recovery",
]


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _turn_index(record: dict) -> int:
    """The turn number out of the run id, which ends in ``/tN`` for a session."""
    tail = record["run_id"].rsplit("/", 1)[-1]
    return int(tail[1:]) if tail.startswith("t") else 0


def _delta(current, previous):
    """A difference that stays empty when either end was not reported."""
    if current is None or previous is None:
        return None
    return current - previous


def turn_rows(arm: str, records: list[dict]) -> list[dict]:
    records = sorted(records, key=_turn_index)
    rows = []
    previous: dict | None = None
    for record in records:
        shadow = record.get("shadow") or {}
        route = record.get("route") or {}
        previous_shadow = (previous or {}).get("shadow") or {}
        rows.append({
            "arm": arm,
            "turn": _turn_index(record),
            "prompt_tokens": record.get("prompt_tokens"),
            "cached_tokens": record.get("cached_tokens"),
            "uncached_tail_tokens": record.get("uncached_suffix_tokens"),
            "foreground_route": route.get("route"),
            "ttft_s": record.get("ttft_s"),
            "prefill_s": record.get("prefill_s"),
            "e2e_s": record.get("e2e_s"),
            "output_tokens": record.get("output_tokens"),
            "output_sha": (record.get("correctness") or {}).get("output_sha256"),
            "context_growth_tokens": _delta(
                record.get("prompt_tokens"), (previous or {}).get("prompt_tokens")
            ),
            "recovery_service_s": shadow.get("service_s"),
            "recovery_processed_tokens": shadow.get("processed_tokens"),
            "canonical_committed_tokens": shadow.get("committed_tokens"),
            "canonical_progress_tokens": _delta(
                shadow.get("committed_tokens"), previous_shadow.get("committed_tokens")
            ),
            "canonical_debt_tokens": shadow.get("canonical_debt_tokens"),
            "publications": shadow.get("publishes"),
            "recovery_between_turns_tokens": _delta(
                shadow.get("processed_tokens"), previous_shadow.get("processed_tokens")
            ),
        })
        previous = record
    return rows


def publication_restores(trace: list[dict]) -> list[dict]:
    """Every publication, paired with the first later fetch that matched it.

    Both ends are the runtime's own records. A publication whose boundary no
    later fetch reaches is kept with an empty restore, because a publication
    that was never consumed is the result the round is most at risk of
    overlooking.
    """
    published = [
        {"seq": event["seq"], "boundary": event.get("boundary"),
         "tokens": event.get("tokens")}
        for event in trace if event.get("event") == "shadow.publish"
    ]
    fetches = [
        event for event in trace
        if event.get("event") == "foreground.fetch"
        and event.get("matched_tokens") is not None
    ]
    rows = []
    for entry in published:
        boundary = entry["boundary"]
        restored = None
        if boundary is not None:
            for fetch in fetches:
                if fetch["seq"] > entry["seq"] and fetch["matched_tokens"] >= boundary:
                    restored = fetch
                    break
        rows.append({
            "publish_seq": entry["seq"],
            "boundary_tokens": boundary,
            "restore_seq": restored["seq"] if restored else None,
            "restore_matched_tokens": restored.get("matched_tokens") if restored else None,
            "restore_prompt_tokens": restored.get("prompt_tokens") if restored else None,
            "restore_remaining_tokens": restored.get("remaining") if restored else None,
        })
    return rows


def classify(rows: list[dict]) -> str:
    """Recovery rate against context growth, as a class and not a ratio.

    One session of five turns does not support a rate, and the question the
    round asks is which side of the growth the recovery lands on.
    """
    pairs = [
        (row["context_growth_tokens"], row["recovery_between_turns_tokens"])
        for row in rows
        if row["context_growth_tokens"] and row["recovery_between_turns_tokens"] is not None
    ]
    if not pairs:
        return "no recovery service"
    recovered = sum(recovery for _, recovery in pairs)
    grown = sum(growth for growth, _ in pairs)
    if recovered == 0:
        return "no recovery service"
    if recovered > grown * 1.05:
        return "recovery > growth"
    if recovered < grown * 0.95:
        return "recovery < growth"
    return "recovery ~= growth"


def summary_row(arm: str, rows: list[dict], restores: list[dict]) -> dict:
    last = rows[-1]
    services = [row["recovery_service_s"] for row in rows if row["recovery_service_s"] is not None]
    processed = [row["recovery_processed_tokens"] for row in rows
                 if row["recovery_processed_tokens"] is not None]
    publishes = [row["publications"] for row in rows if row["publications"] is not None]
    return {
        "arm": arm,
        "turns": len(rows),
        "final_prompt_tokens": last["prompt_tokens"],
        "cumulative_foreground_s": round(sum(row["e2e_s"] for row in rows), 3),
        # The runtime's counters are cumulative, so the session total is the
        # last reading and not a sum over the turns.
        "total_recovery_service_s": round(max(services), 3) if services else None,
        "total_recovered_tokens": max(processed) if processed else None,
        "total_publications": max(publishes) if publishes else None,
        # Turns, not publications: two boundaries restored by the same request
        # are one turn that restored published state, and counting the
        # publications instead would report more reuse than happened.
        "turns_restoring_published_state": len({
            row["restore_seq"] for row in restores if row["restore_seq"] is not None
        }) if restores else (0 if publishes else None),
        "growth_vs_recovery": classify(rows),
    }


def write_csv(path: pathlib.Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: ("" if row.get(key) is None else row[key])
                             for key in columns})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec-runs", required=True)
    parser.add_argument("--pcsr-runs", required=True)
    parser.add_argument("--pcsr-trace", default=None)
    parser.add_argument("--out-dir", default="data/pcsr-agent-validation")
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    spec = turn_rows("spec", read_jsonl(pathlib.Path(args.spec_runs)))
    pcsr = turn_rows("pcsr", read_jsonl(pathlib.Path(args.pcsr_runs)))

    restores: list[dict] = []
    if args.pcsr_trace:
        restores = publication_restores(read_jsonl(pathlib.Path(args.pcsr_trace)))
        write_csv(out / "zero-idle-publications.csv",
                  ["publish_seq", "boundary_tokens", "restore_seq",
                   "restore_matched_tokens", "restore_prompt_tokens",
                   "restore_remaining_tokens"], restores)

    write_csv(out / "zero-idle-turns.csv", TURN_COLUMNS, spec + pcsr)
    write_csv(out / "zero-idle-summary.csv", SUMMARY_COLUMNS, [
        summary_row("spec", spec, []),
        summary_row("pcsr", pcsr, restores),
    ])
    print(f"wrote {len(spec) + len(pcsr)} turn rows and 2 summary rows to {out}")


if __name__ == "__main__":
    main()
