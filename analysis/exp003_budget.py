"""EXP-003 / PCSR — the recovery-budget sweep table.

    uv run python -m analysis.exp003_budget RUNS.json

Writes `data/exp-003/budget-sweep.csv`. The column that matters is the gap
between the budget requested and the share actually received: a budget is a
ceiling on service, not a reservation, and it is enforced between chunks, so a
cell can exceed its own ceiling by part of one chunk. Both numbers are
reported and neither is derived from the other.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

OUT = pathlib.Path("data/exp-003/budget-sweep.csv")
FIELDS = (
    "cell", "budget_pct", "actual_service_s", "actual_service_share",
    "cumulative_foreground_s", "turn0_ttft_s", "turn1_ttft_s", "turn2_ttft_s",
    "canonical_prefix_tokens", "canonical_debt_tokens", "publishes",
    "probe_ttft_s",
)


def _cell(value):
    """Empty only for an absent value. A zero here is a measurement: it is the
    0% cell reporting that the recovery job was admitted and never served."""
    return "" if value is None else value


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m analysis.exp003_budget RUNS.json", file=sys.stderr)
        return 2
    data = json.loads(pathlib.Path(argv[1]).read_text())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for cell, payload in data.items():
            if cell == "meta":
                continue
            turns = payload["turns"]
            writer.writerow({
                "cell": cell,
                "budget_pct": payload["budget_pct"],
                "actual_service_s": _cell(payload.get("actual_service_s")),
                "actual_service_share": _cell(payload.get("actual_service_share")),
                "cumulative_foreground_s": payload["cumulative_foreground_s"],
                "turn0_ttft_s": turns[0]["ttft_s"] if len(turns) > 0 else "",
                "turn1_ttft_s": turns[1]["ttft_s"] if len(turns) > 1 else "",
                "turn2_ttft_s": turns[2]["ttft_s"] if len(turns) > 2 else "",
                "canonical_prefix_tokens": payload["canonical_prefix"],
                "canonical_debt_tokens": payload["canonical_debt"],
                "publishes": _cell(payload.get("publishes")),
                "probe_ttft_s": payload["probe_ttft_s"],
            })
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
