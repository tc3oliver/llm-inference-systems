"""What a parked canonical-recovery state costs, and what retiring it returns.

    uv run python -m analysis.recovery_state_retirement

Reads `data/recovery-foreground-qos/recovery-state-retirement.csv` and
`recovery-state-headroom-probe.csv` and prints the three quantities the
dataset exists to support, each labelled with what it is worth.

The script derives nothing it does not print the inputs for, and it refuses to
report a per-token figure unless the three prompt sizes are exactly collinear —
the whole value of the measurement is that the growth term has no curvature,
and a fitted line through scattered points would hide that.

Three separate things live here and the ladder in `EVIDENCE.md` grades them
differently:

* **Measured** — the retained state's size, and the fall in
  `mx.get_active_memory()` when the reference goes.
* **Derived** — the production geometry's per-token cost, which is arithmetic
  over a config and not a measurement of that model.
* **Not established** — any effect on foreground headroom. The B'' control is
  why: before it existed, B'-to-D read as a multi-gigabyte effect of holding
  the state, and it is reclaim lag.
"""

from __future__ import annotations

import csv
import pathlib

DATA = pathlib.Path("data/recovery-foreground-qos")
RETIREMENT = DATA / "recovery-state-retirement.csv"
HEADROOM = DATA / "recovery-state-headroom-probe.csv"

MIB = 1024 * 1024

# The served model's cache geometry, read from its config. Nothing in this
# file measures it; it is here so the derivation below is checkable.
PRODUCTION = {
    "name": "Qwen3.8-27B-oQ4e-mtp",
    "layers": 64,
    "full_attention_interval": 4,
    "kv_heads": 4,
    "head_dim": 256,
    "bytes_per_element": 2,
}


def _rows(path: pathlib.Path) -> list[dict]:
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _by_point(rows: list[dict], tokens: int) -> dict[str, dict]:
    return {r["point"]: r for r in rows if int(r["prompt_tokens"]) == tokens}


def retained_state_law(rows: list[dict]) -> tuple[int, float, list[int]]:
    """Return (fixed_bytes, bytes_per_token, sizes) from point B at each size.

    Raises if the three points are not collinear. Two points define a line;
    the third is the test, and without it the fixed term and the slope are not
    separable from a single measurement.
    """
    sizes = sorted({int(r["prompt_tokens"]) for r in rows})
    b = [(n, int(_by_point(rows, n)["B"]["state_cache_bytes"])) for n in sizes]
    (n0, y0), (n1, y1) = b[0], b[-1]
    slope = (y1 - y0) / (n1 - n0)
    fixed = y0 - slope * n0
    for n, y in b:
        predicted = fixed + slope * n
        if abs(predicted - y) > 1:  # bytes, not a tolerance band
            raise SystemExit(
                f"point B is not linear in prompt_tokens: {n} tokens "
                f"measured {y} B, the line through the ends predicts "
                f"{predicted:.0f} B. Report the three sizes, not a law."
            )
    return round(fixed), slope, sizes


def main() -> int:
    rows = _rows(RETIREMENT)
    fixed, per_token, sizes = retained_state_law(rows)

    print("Retained state — measured")
    print(f"  sizes: {', '.join(str(n) for n in sizes)} tokens, 512-token slices")
    for n in sizes:
        r = _by_point(rows, n)["B"]
        print(
            f"  {n:>6} tok  {int(r['state_cache_bytes']) / MIB:8.2f} MiB"
            f"  = {int(r['state_arrayscache_bytes']) / MIB:6.2f} ArraysCache"
            f"  + {int(r['state_kvcache_bytes']) / MIB:7.2f} KVCache"
        )
    print(
        f"  law: {fixed / MIB:.2f} MiB fixed + {per_token / 1024:.0f} KiB/token"
        "  (exact at all three sizes)"
    )

    print()
    print("Release — measured")
    for n in sizes:
        pts = _by_point(rows, n)
        a, b, c = pts["A"], pts["B"], pts["C"]
        held = int(b["mlx_active_bytes"]) - int(a["mlx_active_bytes"])
        given = int(b["mlx_active_bytes"]) - int(c["mlx_active_bytes"])
        state = int(b["state_cache_bytes"])
        print(
            f"  {n:>6} tok  active rose {held / MIB:8.2f} MiB over A,"
            f"  fell {given / MIB:8.2f} MiB at C,"
            f"  state {state / MIB:8.2f} MiB"
        )
    print("  mx.get_active_memory() returns the state's bytes when the reference goes.")

    print()
    print("Physical footprint — not established")
    for n in sizes:
        pts = _by_point(rows, n)
        for p in ("B'", "B''", "C", "D"):
            r = pts[p]
            print(
                f"  {n:>6} tok  {p:<4} phys"
                f" {int(r['physical_footprint_bytes']) / MIB:9.1f} MiB"
                f"  state_resident={r['state_resident']}"
            )
        gap = (
            int(pts["B''"]["physical_footprint_bytes"])
            - int(pts["D"]["physical_footprint_bytes"])
        )
        print(
            f"  {n:>6} tok  B''-D = {gap / MIB:.1f} MiB against a state of"
            f" {int(pts['B']['state_cache_bytes']) / MIB:.1f} MiB"
        )
    print(
        "  B' is an unconverged reading. B'' is the control: with the state\n"
        "  still held, phys has already reached D. The B'-to-D difference is\n"
        "  reclaim lag, not the cost of holding the state."
    )

    print()
    print("Foreground headroom probe — not established")
    for r in _rows(HEADROOM):
        print(
            f"  {r['arm']:<7} rep{r['repetition']}"
            f"  settled phys {int(r['settled_physical_footprint_bytes']) / MIB:8.1f} MiB"
            f"  probe growth {int(r['probe_physical_growth_bytes']) / MIB:7.1f} MiB"
        )
    print(
        "  The two arms differ in the direction the mechanism predicts and by\n"
        "  less than the retained state, against a settled-phys spread between\n"
        "  repetitions of the same arm that is larger than the effect sought."
    )

    print()
    print("Production geometry — derived")
    p = PRODUCTION
    full = p["layers"] // p["full_attention_interval"]
    per_tok = full * 2 * p["kv_heads"] * p["head_dim"] * p["bytes_per_element"]
    print(
        f"  {p['name']}: {p['layers']} layers, interval"
        f" {p['full_attention_interval']} -> {full} full-attention layers"
    )
    print(
        f"  {full} x 2 x {p['kv_heads']} x {p['head_dim']} x"
        f" {p['bytes_per_element']} = {per_tok // 1024} KiB/token"
    )
    for ctx in (32_768, 131_072):
        print(f"  {ctx:>7} tokens -> {ctx * per_tok / (1024 ** 3):.1f} GiB parked")
    print(
        "  Arithmetic over that model's config, plus a fixed recurrent-state\n"
        "  term this harness did not measure for that geometry. Not a\n"
        "  measurement of that model."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
