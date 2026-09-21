"""EXP-003 — turn the four-arm session records into the tables the study reads.

    uv run python -m analysis.exp003 RUNS.json
    uv run python -m analysis.exp003 RUNS.json PREFIX --dense-break-even 8192

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

## Spec Exit

PCSR republishes canonical prefix state that a sparse prefill failed to create.
The question those columns answer is not whether recovery happens but whether
it outruns the session: once the canonical prefix is close enough behind the
prompt, the next turn's uncached tail is small enough to prefill densely, and
the foreground stops needing SpecPrefill at all. The turn where that becomes
permanent is the Spec Exit.

Four derivations, in the order they depend on each other:

    canonical_debt   = prompt_tokens - canonical_prefix_tokens
    uncached_tail    = prompt_tokens - cached_tokens
    debt_delta[i]    = debt[i+1] - debt[i]
    catch_up_ratio   = delta_committed_canonical_tokens
                       / delta_required_context_tokens

`uncached_tail` is deliberately not the runtime's `uncached_suffix`: it is what
the request had to compute, taken from the two counters that bracket it, and
both columns are written so a disagreement between them stays visible.

The last two describe the interval that *starts* at their turn, so both are
empty on the final turn — an interval needs the turn after it. They are also
one identity: `debt_delta = delta_required * (1 - catch_up_ratio)`, so a ratio
above 1 is a turn where the debt shrank and a ratio below 1 is a turn where it
grew. A ratio over a zero-length interval is undefined rather than zero or
infinite, and is written empty.

The route a turn would take follows from the tail alone:

    route = CACHE_HIT     if uncached_tail == 0
            DENSE         if uncached_tail <= dense_break_even
            SPECPREFILL   otherwise

    spec_exit_turn = the first turn index T whose route is not SPECPREFILL and
                     where no later turn's is either; empty if there is none.

Turn indices are the runner JSON's own, which are 0-based, so a `spec_exit_turn`
of 0 is a session that never took the sparse path at all.

## Recorded against derived

The runtime now records the route it actually took, at admission, together with
the numbers it decided on. That record wins: the `route` column reports it,
normalised to the names above, and falls back to the derivation only where
there is none. `route_source` says which of the two produced the value, so no
reader has to work it out from the shape of the row.

`route_disagrees` is the reason both are computed. It is true when a recorded
route is present and the derived route contradicts it, and empty when there was
nothing to compare — a disagreement means the runtime's definition and this
module's have drifted apart, which is a finding and not a bug in the table. It
is not repaired by bending the derivation to match, because a derivation tuned
to agree can no longer detect anything.

`dense_break_even` is an input, never a constant here, and four things can
supply it. In order:

1. `route.threshold_tokens`, the threshold actually in force for that request.
   It is per turn, so each turn is classified against its own.
2. the arm's `dense_break_even_tokens`.
3. `meta.dense_break_even_tokens`.
4. `--dense-break-even`, for a run recorded before any of the above existed.

With none of them, `route` falls back to empty rather than guessed, unless the
runtime recorded one. The arm summary names a break-even only when a single one
was in force across the arm; turns recorded under thresholds that differ leave
that column empty, because there is then no one number to name, even though
every turn was still classified against its own.

The one derived route that needs no break-even is `CACHE_HIT`: a zero tail is
not a judgement about cost. A turn whose route cannot be decided — empty, or a
name this module does not know — empties `spec_exit_turn` for the whole arm,
because an undecided turn could have been the SpecPrefill the exit had to come
after.

## Which cache count the tail is measured against

`uncached_tail` prefers `route.cached_tokens`, the post-restore count the
admission itself compared, over the `cached_tokens` the usage object reports.
A SpecPrefill turn reports a usage `cached_tokens` of 0 whatever the cache
held, so on exactly the turns this study is about the usage figure understates
reuse and the tail computed from it is the whole prompt.
`cached_tokens_source` says which count was used, and both are written.

The probe is excluded from all of it. It is an instrument run after the
session, and a debt delta across that boundary would measure the instrument.
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys

OUT_DIR = pathlib.Path("data/exp-003")

ROUTE_CACHE_HIT = "CACHE_HIT"
ROUTE_DENSE = "DENSE"
ROUTE_SPECPREFILL = "SPECPREFILL"
KNOWN_ROUTES = (ROUTE_CACHE_HIT, ROUTE_DENSE, ROUTE_SPECPREFILL)

# The server spells its routes in lower case. Anything it reports that is not
# in this map is written through rather than dropped, so a runtime that grows
# a fourth route is visible here instead of silently becoming a derivation.
RECORDED_ROUTES = {
    "cache_hit": ROUTE_CACHE_HIT,
    "dense": ROUTE_DENSE,
    "specprefill": ROUTE_SPECPREFILL,
}


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
    "canonical_prefix_tokens", "canonical_debt_tokens", "uncached_tail_tokens",
    "debt_delta_tokens", "catch_up_ratio", "route",
    "route_source", "route_disagrees", "route_cached_tokens",
    "route_tail_tokens", "cached_tokens_source",
)
DERIVED_TURN_FIELDS = (
    "canonical_prefix_tokens", "canonical_debt_tokens", "uncached_tail_tokens",
    "debt_delta_tokens", "catch_up_ratio", "route",
    "route_source", "route_disagrees", "route_cached_tokens",
    "route_tail_tokens", "cached_tokens_source",
)
SUMMARY_FIELDS = (
    "arm", "cumulative_foreground_s", "probe_ttft_s",
    "longest_canonical_prefix_tokens", "canonical_debt_tokens",
    "final_prompt_tokens", "shadow_service_s", "shadow_publishes",
    "turns", "spec_exit_turn", "final_canonical_prefix_tokens",
    "final_canonical_debt_tokens", "mean_catch_up_ratio",
    "measured_recovery_share", "dense_break_even_tokens",
    "route_disagreements",
)


def _cell(value):
    """Empty only for an absent value. A zero here is a measurement: it is a
    turn whose debt did not move, or whose tail the cache covered entirely."""
    return "" if value is None else value


def _shadow(row: dict, key: str):
    value = (row.get("shadow") or {}).get(key)
    return "" if value is None else value


def _recorded(row: dict, key: str):
    """One field of the server's admission record, nested or flattened.

    The JSONL run record carries a `route` object. The runner JSON this script
    reads flattens it to `route_<field>` keys, with `route` itself holding the
    route name. Both spellings are read, so a table can be built from either
    without a conversion step in between.
    """
    block = row.get("route")
    if isinstance(block, dict):
        return block.get(key)
    return row.get(f"route_{key}")


def recorded_route(row: dict):
    """The route the server took, in this module's names, or None.

    A name the server reports that this module does not know is written
    through in upper case rather than discarded. Calling it a derivation would
    be false, and dropping it would hide the only evidence that the runtime
    grew a route this analysis has never heard of.
    """
    block = row.get("route")
    value = block.get("route") if isinstance(block, dict) else block
    if value is None:
        return None
    return RECORDED_ROUTES.get(str(value).lower(), str(value).upper())


def recorded_threshold_tokens(row: dict):
    """The break-even actually in force for this request, or None."""
    value = _recorded(row, "threshold_tokens")
    return int(value) if value is not None else None


def cached_tokens_for_tail(row: dict) -> tuple[int | None, str | None]:
    """(tokens, source) — the cache count the uncached tail is measured from.

    The admission's own post-restore count wins over the usage object's,
    because a SpecPrefill turn reports a usage `cached_tokens` of 0 whatever
    the cache held.
    """
    recorded = _recorded(row, "cached_tokens")
    if recorded is not None:
        return int(recorded), "recorded"
    value = row.get("cached_tokens")
    if value is None:
        return None, None
    return int(value), "usage"


# ------------------------------------------------------------- derivations


def canonical_debt(prompt_tokens, canonical_prefix_tokens):
    """How far the canonical prefix is behind the prompt, in tokens."""
    if prompt_tokens is None or canonical_prefix_tokens is None:
        return None
    return int(prompt_tokens) - int(canonical_prefix_tokens)


def uncached_tail(prompt_tokens, cached_tokens):
    """What the request actually had to compute."""
    if prompt_tokens is None or cached_tokens is None:
        return None
    return int(prompt_tokens) - int(cached_tokens)


def catch_up_ratio(delta_committed_canonical_tokens, delta_required_context_tokens):
    """Canonical state gained per token of context gained.

    A zero-length interval leaves the ratio undefined, which is None here and
    an empty cell in the table. Reporting it as 0 would say recovery stalled
    and reporting it as infinite would say it won, and the interval says
    neither.
    """
    if delta_committed_canonical_tokens is None or delta_required_context_tokens is None:
        return None
    if delta_required_context_tokens == 0:
        return None
    return delta_committed_canonical_tokens / delta_required_context_tokens


def classify_route(uncached_tail_tokens, dense_break_even):
    """The path a turn with this tail takes, or None when it cannot be decided.

    A zero tail is CACHE_HIT without consulting the break-even, because there
    is nothing to prefill either way. Every other tail is a cost comparison and
    needs the break-even the build and the model set.
    """
    if uncached_tail_tokens is None:
        return None
    if uncached_tail_tokens == 0:
        return ROUTE_CACHE_HIT
    if dense_break_even is None:
        return None
    if uncached_tail_tokens <= dense_break_even:
        return ROUTE_DENSE
    return ROUTE_SPECPREFILL


def spec_exit_turn(routes, turn_indices=None):
    """The first turn after which SpecPrefill is never taken again.

    None when the session never leaves SpecPrefill, when it has no turns, and
    when any turn's route is undecided — empty, or a name this module does not
    know how to reason about. An undecided turn could have been the SpecPrefill
    the exit would have to follow, so there is no exit to report rather than an
    exit at turn 0.
    """
    routes = list(routes)
    if not routes or any(route not in KNOWN_ROUTES for route in routes):
        return None
    indices = list(turn_indices) if turn_indices is not None else list(range(len(routes)))
    sparse = [position for position, route in enumerate(routes)
              if route == ROUTE_SPECPREFILL]
    if not sparse:
        return indices[0]
    if sparse[-1] == len(routes) - 1:
        return None
    return indices[sparse[-1] + 1]


def canonical_prefix_tokens(row: dict):
    """The longest prefix this turn's canonical state covers.

    Three places can carry it, tried in order of directness: the turn's own
    field, the shadow job's published prefix, and the tokens it has committed.
    A run that reports only `canonical_debt_tokens` still fixes the prefix,
    because debt and prefix are one identity; reading it back that way keeps
    every derived column on a single source instead of mixing two that can
    disagree.
    """
    shadow = row.get("shadow") or {}
    for value in (row.get("canonical_prefix_tokens"),
                  shadow.get("longest_canonical_prefix_tokens"),
                  shadow.get("committed_tokens")):
        if value is not None:
            return int(value)
    debt = shadow.get("canonical_debt_tokens")
    prompt = row.get("prompt_tokens")
    if debt is not None and prompt is not None:
        return int(prompt) - int(debt)
    return None


def session_turns(payload: dict) -> list[dict]:
    """The arm's turns with the dense probe left out."""
    return [row for row in (payload.get("turns") or [])
            if row.get("kind") != "dense-probe"]


def derive_turns(turns: list[dict], dense_break_even=None) -> list[dict]:
    """The derived columns for one arm's session turns, aligned with `turns`.

    `dense_break_even` is the arm's fallback. A turn that recorded the
    threshold it was admitted under is classified against that instead, so a
    sweep whose cells ran under different thresholds is still read correctly.
    """
    rows = list(turns)
    prefixes = [canonical_prefix_tokens(row) for row in rows]
    prompts = [row.get("prompt_tokens") for row in rows]
    debts = [canonical_debt(prompt, prefix)
             for prompt, prefix in zip(prompts, prefixes)]
    cached = [cached_tokens_for_tail(row) for row in rows]
    tails = [uncached_tail(prompt, count)
             for prompt, (count, _) in zip(prompts, cached)]

    derived = []
    for position, row in enumerate(rows):
        nxt = position + 1
        delta_debt = delta_canonical = delta_required = None
        if nxt < len(rows):
            if debts[position] is not None and debts[nxt] is not None:
                delta_debt = debts[nxt] - debts[position]
            if prefixes[position] is not None and prefixes[nxt] is not None:
                delta_canonical = prefixes[nxt] - prefixes[position]
            if prompts[position] is not None and prompts[nxt] is not None:
                delta_required = int(prompts[nxt]) - int(prompts[position])

        threshold = recorded_threshold_tokens(row)
        if threshold is None:
            threshold = dense_break_even
        computed = classify_route(tails[position], threshold)
        observed = recorded_route(row)
        comparable = observed is not None and computed is not None
        derived.append({
            "canonical_prefix_tokens": prefixes[position],
            "canonical_debt_tokens": debts[position],
            "uncached_tail_tokens": tails[position],
            "debt_delta_tokens": delta_debt,
            "catch_up_ratio": catch_up_ratio(delta_canonical, delta_required),
            "route": observed if observed is not None else computed,
            "route_source": ("recorded" if observed is not None
                             else ("derived" if computed is not None else None)),
            "route_disagrees": (observed != computed) if comparable else None,
            "route_cached_tokens": _recorded(row, "cached_tokens"),
            "route_tail_tokens": _recorded(row, "tail_tokens"),
            "cached_tokens_source": cached[position][1],
        })
    return derived


def measured_recovery_share(payload: dict):
    """The share of wall time the recovery job actually received.

    An arm may report it once for the session. Otherwise the runtime's counter
    is cumulative, so the last turn that reported one holds the session total;
    the probe counts here, because it runs last and reads the same counter.
    """
    if payload.get("actual_service_share") is not None:
        return payload["actual_service_share"]
    for row in reversed(payload.get("turns") or []):
        share = (row.get("shadow") or {}).get("service_share")
        if share is not None:
            return share
    return None


def dense_break_even_tokens(data: dict, arm_payload: dict | None = None,
                            override=None):
    """The one break-even in force across an arm, or None if there is no one.

    The threshold the runtime recorded at admission wins, because it is the
    number the decision was actually made against. It is per request, so it
    names an arm only when every turn that recorded one recorded the same; an
    arm whose turns ran under thresholds that differ has no single break-even
    to report and this returns None, while `derive_turns` still classifies
    each of those turns against its own.

    After that the run record wins, per arm and then `meta`, and the override
    is for a record written before any of the fields existed. There is no
    default: a guessed break-even would put a made-up boundary in `route`.
    """
    payload = arm_payload or {}
    recorded = {recorded_threshold_tokens(row)
                for row in (payload.get("turns") or [])}
    recorded.discard(None)
    if recorded:
        return recorded.pop() if len(recorded) == 1 else None
    for source in (payload, (data or {}).get("meta") or {}):
        value = source.get("dense_break_even_tokens")
        if value is not None:
            return int(value)
    return int(override) if override is not None else None


def arm_summary(payload: dict, dense_break_even=None) -> dict:
    """The Spec Exit row for one arm.

    `mean_catch_up_ratio` is a mean over one session's turns, not over repeats
    — the repository's summaries refuse a mean across repeats, and this is not
    one. Every value it averages is in the turns table beside it.

    `route_disagreements` counts the turns where the route the runtime
    recorded and the route this module derives contradict each other, so the
    drift is visible without reading the turns table. It is empty when no turn
    carried both, which is not the same as a session where the two agreed.
    """
    rows = session_turns(payload)
    derived = derive_turns(rows, dense_break_even)
    indices = [row.get("turn") if row.get("turn") is not None else position
               for position, row in enumerate(rows)]
    ratios = [entry["catch_up_ratio"] for entry in derived
              if entry["catch_up_ratio"] is not None]
    compared = [entry["route_disagrees"] for entry in derived
                if entry["route_disagrees"] is not None]
    final = derived[-1] if derived else {}
    return {
        "turns": len(rows),
        "spec_exit_turn": spec_exit_turn([e["route"] for e in derived], indices),
        "final_canonical_prefix_tokens": final.get("canonical_prefix_tokens"),
        "final_canonical_debt_tokens": final.get("canonical_debt_tokens"),
        "mean_catch_up_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "cumulative_foreground_s": payload.get("cumulative_foreground_s"),
        "measured_recovery_share": measured_recovery_share(payload),
        "dense_break_even_tokens": dense_break_even,
        # Empty when no turn had both a recorded and a derived route to
        # compare, which is not the same as a session where the two agreed.
        "route_disagreements": sum(compared) if compared else None,
    }


# --------------------------------------------------------------------- CLI


def _parse_args(argv: list[str]) -> tuple[list[str], int | None]:
    """The two positional arguments, unchanged, plus one optional flag.

    argparse is avoided so that `python -m analysis.exp003 RUNS.json` and
    `... RUNS.json PREFIX` keep behaving exactly as the commands already
    recorded elsewhere expect.
    """
    positional: list[str] = []
    override: int | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--dense-break-even":
            index += 1
            if index >= len(argv):
                raise ValueError("--dense-break-even needs a token count")
            override = int(argv[index])
        elif arg.startswith("--dense-break-even="):
            override = int(arg.split("=", 1)[1])
        else:
            positional.append(arg)
        index += 1
    return positional, override


def main(argv: list[str]) -> int:
    try:
        positional, override = _parse_args(argv[1:])
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if not positional:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    data = json.loads(pathlib.Path(positional[0]).read_text())
    turns_out, summary_out = _outputs(positional[1] if len(positional) > 1 else "")
    arms = {k: v for k, v in data.items() if k != "meta"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    break_even = {
        arm: dense_break_even_tokens(data, payload, override)
        for arm, payload in arms.items()
    }

    with turns_out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TURN_FIELDS, lineterminator="\n")
        writer.writeheader()
        for arm, payload in arms.items():
            rows = payload["turns"]
            # The probe keeps its row in the table and takes no derived
            # columns: it is an instrument, and a debt delta measured across
            # it would be a property of the instrument.
            positions = [i for i, row in enumerate(rows)
                         if row.get("kind") != "dense-probe"]
            derived = dict(zip(positions, derive_turns(
                [rows[i] for i in positions], break_even[arm])))
            for position, row in enumerate(rows):
                extra = derived.get(position, {})
                writer.writerow({
                    "arm": arm,
                    "kind": row.get("kind"),
                    "turn": row.get("turn"),
                    "prompt_tokens": row.get("prompt_tokens"),
                    "cached_tokens": row.get("cached_tokens"),
                    "uncached_suffix": row.get("uncached_suffix"),
                    "ttft_s": row.get("ttft_s"),
                    "prefill_s": _cell(row.get("prefill_s")),
                    "decode_s": row.get("decode_s"),
                    "decode_tps": _cell(row.get("decode_tps")),
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
                    "shadow_canonical_debt_tokens": _shadow(
                        row, "canonical_debt_tokens"),
                    **{field: _cell(extra.get(field))
                       for field in DERIVED_TURN_FIELDS},
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
                # The Spec Exit columns only. `cumulative_foreground_s` is
                # part of the summary row but is already written above from
                # the same field, and the column above stays the one that
                # decides it.
                **{field: _cell(value) for field, value
                   in arm_summary(payload, break_even[arm]).items()
                   if field != "cumulative_foreground_s"},
            })

    print(f"wrote {turns_out} and {summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
