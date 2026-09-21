# SPDX-License-Identifier: Apache-2.0
"""Sanitized per-request table from one observational Claude Code session.

The runtime's shadow trace is the only source. It carries token counts, block
hashes and a monotonic clock, and no prompt text, tool result or completion —
which is why the derived tables here can be published while the session's raw
log cannot.

One request is one ``foreground.arrival`` followed by the ``foreground.fetch``
that reports what the prefix cache matched for it. The interval between two
arrivals is the gap the background recovery had to work in, and every
``shadow.slice`` whose execution falls inside that interval is service the
scheduler actually granted there. A slice is uninterruptible, so a slice is
attributed to the gap its start falls in.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib

REQUEST_COLUMNS = [
    "req", "arrival_s", "prompt_tokens", "restored_prefix_tokens",
    "uncached_tail_tokens", "inter_request_gap_s", "recovery_service_s",
    "recovery_slices", "recovery_tokens", "publications",
    "canonical_committed_tokens", "reused_a_publication",
]

SUMMARY_COLUMNS = [
    "foreground_requests", "gaps", "gaps_with_recovery", "gap_min_s",
    "gap_median_s", "gap_max_s", "total_recovery_service_s",
    "total_recovery_tokens", "total_publications",
    "publications_restored_attributably",
    "publications_reached_by_a_later_restore", "lineage_resets",
]


def read_jsonl(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _median(values: list[float]):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def requests_from(trace: list[dict]) -> list[dict]:
    """Arrivals, each carrying the fetch that followed it.

    An arrival with no fetch after it is dropped rather than filled: it is a
    request the scheduler saw and the prefix cache never reported on, and a row
    of empty cells would read as a measurement of zero reuse.
    """
    arrivals = [event for event in trace if event.get("event") == "foreground.arrival"]
    fetches = {}
    for event in trace:
        if event.get("event") == "foreground.fetch":
            fetches.setdefault(event.get("request_id"), event)
    rows = []
    for arrival in arrivals:
        fetch = fetches.get(arrival.get("request_id"))
        if fetch is None:
            continue
        rows.append({
            "seq": arrival["seq"],
            "at_s": arrival.get("at_s"),
            "prompt_tokens": fetch.get("prompt_tokens"),
            "matched_tokens": fetch.get("matched_tokens"),
            "remaining": fetch.get("remaining"),
        })
    return rows


def slices_from(trace: list[dict]) -> list[dict]:
    return [
        {
            "seq": event["seq"],
            "started_s": event.get("started_s"),
            "ended_s": event.get("ended_s"),
            "duration_s": event.get("duration_s"),
            "tokens_before": event.get("tokens_before"),
            "tokens_after": event.get("tokens_after"),
            "committed_before": event.get("committed_before"),
            "committed_after": event.get("committed_after"),
            "published": event.get("published"),
        }
        for event in trace if event.get("event") == "shadow.slice"
    ]


def publications_from(trace: list[dict]) -> list[dict]:
    return [
        {"seq": event["seq"], "boundary": event.get("boundary"),
         "tokens": event.get("tokens")}
        for event in trace if event.get("event") == "shadow.publish"
    ]


def publication_rows(trace: list[dict]) -> list[dict]:
    """Each publication against the very next fetch, not the next matching one.

    "Some later request matched at least this boundary" is too weak a test in a
    real session: the ordinary write-back path is running as well, and once it
    is ahead of the recovery every fetch clears every boundary. The next fetch
    matching a boundary *exactly* is the case where the recovery is the only
    thing that could have put that prefix there.
    """
    publications = publications_from(trace)
    fetches = [
        event for event in trace
        if event.get("event") == "foreground.fetch"
        and event.get("matched_tokens") is not None
    ]
    rows = []
    for entry in publications:
        following = [f for f in fetches if f["seq"] > entry["seq"]]
        nxt = following[0] if following else None
        matched = nxt.get("matched_tokens") if nxt else None
        rows.append({
            "publish_seq": entry["seq"],
            "boundary_tokens": entry["boundary"],
            "next_fetch_seq": nxt["seq"] if nxt else None,
            "next_fetch_prompt_tokens": nxt.get("prompt_tokens") if nxt else None,
            "next_fetch_matched_tokens": matched,
            "next_fetch_remaining_tokens": nxt.get("remaining") if nxt else None,
            "attributable": matched == entry["boundary"],
        })
    return rows


def build_rows(trace: list[dict]) -> tuple[list[dict], dict]:
    requests = requests_from(trace)
    slices = slices_from(trace)
    publications = publications_from(trace)

    rows = []
    previous = None
    for index, request in enumerate(requests):
        gap = None
        in_gap = []
        if previous is not None and previous["at_s"] is not None \
                and request["at_s"] is not None:
            gap = request["at_s"] - previous["at_s"]
            in_gap = [
                entry for entry in slices
                if entry["started_s"] is not None
                and previous["at_s"] <= entry["started_s"] < request["at_s"]
            ]
        published_before = [
            entry for entry in publications if entry["seq"] < request["seq"]
            and entry["boundary"] is not None
        ]
        matched = request["matched_tokens"]
        reused = bool(
            matched is not None
            and any(entry["boundary"] <= matched for entry in published_before)
        )
        committed = [entry["committed_after"] for entry in slices
                     if entry["seq"] < request["seq"]
                     and entry["committed_after"] is not None]
        tokens = [entry["tokens_after"] - entry["tokens_before"] for entry in in_gap
                  if entry["tokens_after"] is not None and entry["tokens_before"] is not None]
        rows.append({
            "req": index,
            "arrival_s": round(request["at_s"], 3) if request["at_s"] is not None else None,
            "prompt_tokens": request["prompt_tokens"],
            "restored_prefix_tokens": matched,
            "uncached_tail_tokens": request["remaining"],
            "inter_request_gap_s": round(gap, 3) if gap is not None else None,
            "recovery_service_s": round(sum(
                entry["duration_s"] for entry in in_gap if entry["duration_s"]
            ), 3) if in_gap else (0.0 if gap is not None else None),
            "recovery_slices": len(in_gap) if gap is not None else None,
            "recovery_tokens": sum(tokens) if tokens else (0 if gap is not None else None),
            "publications": sum(1 for entry in in_gap if entry["published"]) \
                if gap is not None else None,
            "canonical_committed_tokens": max(committed) if committed else None,
            "reused_a_publication": reused,
        })
        previous = request

    gaps = [row["inter_request_gap_s"] for row in rows
            if row["inter_request_gap_s"] is not None]
    # A publication counts as restored when some request that arrived after it
    # matched a prefix reaching its boundary. Requests are keyed by their
    # arrival's sequence number, which is the same counter the publication
    # carries, so "after" is a comparison and not an inference.
    requests_by_seq = [
        (request["seq"], request["matched_tokens"]) for request in requests
    ]
    restored = {
        entry["seq"] for entry in publications
        if entry["boundary"] is not None
        and any(
            matched is not None and seq > entry["seq"] and matched >= entry["boundary"]
            for seq, matched in requests_by_seq
        )
    }
    summary = {
        "foreground_requests": len(rows),
        "gaps": len(gaps),
        "gaps_with_recovery": sum(
            1 for row in rows if (row["recovery_service_s"] or 0) > 0
        ),
        "gap_min_s": min(gaps) if gaps else None,
        "gap_median_s": _median(gaps),
        "gap_max_s": max(gaps) if gaps else None,
        "total_recovery_service_s": round(sum(
            entry["duration_s"] for entry in slices if entry["duration_s"]
        ), 3) if slices else 0.0,
        "total_recovery_tokens": sum(
            entry["tokens_after"] - entry["tokens_before"] for entry in slices
            if entry["tokens_after"] is not None and entry["tokens_before"] is not None
        ) if slices else 0,
        "total_publications": len(publications),
        # Restored at the boundary by the very next fetch — the only case in
        # which the recovery is the only thing that could have put that prefix
        # there. `reused_a_publication` on a request row is the weaker test and
        # counts every later request once the ordinary write-back is ahead.
        "publications_restored_attributably": sum(
            1 for row in publication_rows(trace) if row["attributable"]
        ),
        "publications_reached_by_a_later_restore": len(restored),
        # A reset is a fetch that matched nothing although a publication
        # existed: the lineage the recovery published against is not the one
        # the request arrived on.
        "lineage_resets": sum(
            1 for row in rows
            if row["restored_prefix_tokens"] == 0
            and (row["canonical_committed_tokens"] or 0) > 0
        ),
    }
    return rows, summary


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
    parser.add_argument("--trace", required=True)
    parser.add_argument("--out-dir", default="data/pcsr-agent-validation")
    args = parser.parse_args()

    trace = read_jsonl(pathlib.Path(args.trace))
    rows, summary = build_rows(trace)
    out = pathlib.Path(args.out_dir)
    write_csv(out / "claude-code-requests.csv", REQUEST_COLUMNS, rows)
    write_csv(out / "claude-code-publications.csv",
              ["publish_seq", "boundary_tokens", "next_fetch_seq",
               "next_fetch_prompt_tokens", "next_fetch_matched_tokens",
               "next_fetch_remaining_tokens", "attributable"],
              publication_rows(trace))
    write_csv(out / "claude-code-runtime-summary.csv", SUMMARY_COLUMNS, [summary])
    print(f"wrote {len(rows)} request rows to {out}")


if __name__ == "__main__":
    main()
