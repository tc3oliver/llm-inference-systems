"""EXP-002 — matched speculative-decoding comparisons and the cost-ratio model.

Reads `data/exp-002/runs.csv`, pairs every speculative arm against the dense
arm measured on the same model, content, context and output budget, and writes
`data/exp-002/matched-comparisons.csv`.

    uv run python -m analysis.exp002

Three derived quantities carry the argument.

`tok_per_cycle` is what one verify cycle actually delivers. The runtime emits
`accepted + 1` tokens per cycle, so it is `(accepted + cycles) / cycles`. It is
not `output_tokens / cycles`: a sequence the controller parks back onto the
standard decoder keeps producing tokens that no cycle produced, and dividing by
cycles would credit speculation with them.

`spec_share` is the fraction of the completion speculation actually produced,
`(accepted + cycles) / output_tokens`. For a parked sequence it is small, and
that is the controller's decision made visible.

`cost_ratio` is the price of a cycle in dense steps,
`spec_ms_per_cycle / dense_ms_per_token`, where `spec_ms_per_cycle` sums the
runtime's own backbone, head, sampling and cache-op timers. Speculation pays
for itself on the part of the output it produced exactly when

    tok_per_cycle > cost_ratio

and the predicted whole-request speedup follows from Amdahl over `spec_share`.
Predicted and measured are reported side by side; neither is derived from the
other.
"""

from __future__ import annotations

import csv
import pathlib
import statistics
from typing import Any

RUNS = pathlib.Path("data/exp-002/runs.csv")
OUT = pathlib.Path("data/exp-002/matched-comparisons.csv")
KEY = ("model", "content", "context")


def _num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def load(path: pathlib.Path = RUNS) -> list[dict[str, Any]]:
    return list(csv.DictReader(path.open()))


def _median(rows: list[dict], field: str) -> float | None:
    values = [_num(r[field]) for r in rows if _num(r[field]) is not None]
    return statistics.median(values) if values else None


def _spread(rows: list[dict], field: str) -> float | None:
    """Max-min over the median, as a fraction. The repeat-agreement check."""
    values = [_num(r[field]) for r in rows if _num(r[field]) is not None]
    if len(values) < 2:
        return None
    mid = statistics.median(values)
    return (max(values) - min(values)) / mid if mid else None


def compare(rows: list[dict]) -> list[dict]:
    cells: dict[tuple, dict[str, list[dict]]] = {}
    for row in rows:
        cells.setdefault(tuple(row[k] for k in KEY), {}).setdefault(
            row["policy"], []).append(row)

    out = []
    for key, policies in sorted(cells.items()):
        dense = policies.get("dense")
        if not dense:
            continue
        dense_ms = _median(dense, "decode_ms_per_token")
        dense_e2e = _median(dense, "e2e_s")
        for policy, arm in sorted(policies.items()):
            if policy == "dense":
                continue
            ms = _median(arm, "decode_ms_per_token")
            tpc = _median(arm, "tok_per_cycle")
            share = _median(arm, "spec_share")
            cycle_ms = _median(arm, "spec_ms_per_cycle")
            cost_ratio = cycle_ms / dense_ms if cycle_ms and dense_ms else None
            spec_speedup = tpc / cost_ratio if tpc and cost_ratio else None
            predicted = None
            if spec_speedup and share is not None:
                predicted = 1.0 / (share / spec_speedup + (1.0 - share))
            hashes = {r["output_sha256"] for r in arm}
            dense_hashes = {r["output_sha256"] for r in dense}
            out.append({
                "model": key[0], "content": key[1], "context": key[2],
                "policy": policy,
                "n_dense": len(dense), "n_policy": len(arm),
                "dense_ms_per_token": round(dense_ms, 4) if dense_ms else None,
                "policy_ms_per_token": round(ms, 4) if ms else None,
                "decode_speedup": round(dense_ms / ms, 4) if dense_ms and ms else None,
                "e2e_speedup": round(dense_e2e / _median(arm, "e2e_s"), 4)
                if dense_e2e and _median(arm, "e2e_s") else None,
                "accept_rate": _median(arm, "accept_rate"),
                "tok_per_cycle": round(tpc, 4) if tpc else None,
                "spec_share": round(share, 4) if share is not None else None,
                "spec_ms_per_cycle": round(cycle_ms, 4) if cycle_ms else None,
                "cost_ratio": round(cost_ratio, 4) if cost_ratio else None,
                "profitable_predicted": (tpc > cost_ratio)
                if (tpc and cost_ratio) else None,
                "predicted_speedup": round(predicted, 4) if predicted else None,
                "repeat_spread_policy": round(_spread(arm, "decode_ms_per_token"), 4)
                if _spread(arm, "decode_ms_per_token") is not None else None,
                "repeat_spread_dense": round(_spread(dense, "decode_ms_per_token"), 4)
                if _spread(dense, "decode_ms_per_token") is not None else None,
                "policy_output_reproducible": len(hashes) == 1,
                "dense_output_reproducible": len(dense_hashes) == 1,
                "output_matches_dense": bool(hashes & dense_hashes),
                "decode_time_source": arm[0]["decode_time_source"],
            })
    return out


def write(rows: list[dict], path: pathlib.Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = compare(load())
    write(rows)
    head = (f"{'model':20}{'content':7}{'ctx':6}{'policy':10}"
            f"{'accept':>7}{'tok/cyc':>9}{'cost_r':>8}{'share':>7}"
            f"{'pred':>7}{'measured':>10}{'repro':>7}")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['model']:20}{r['content']:7}{r['context']:6}{r['policy']:10}"
              f"{r['accept_rate'] or '-':>7}{r['tok_per_cycle'] or '-':>9}"
              f"{r['cost_ratio'] or '-':>8}{r['spec_share'] or '-':>7}"
              f"{r['predicted_speedup'] or '-':>7}{r['decode_speedup'] or '-':>10}"
              f"{r['policy_output_reproducible']!s:>7}")
    print(f"\n{len(rows)} matched comparisons -> {OUT}")


if __name__ == "__main__":
    main()
