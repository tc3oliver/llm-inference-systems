"""EXP-003 — turn the four-arm session records into the tables the study reads.

    uv run python -m analysis.exp003 RUNS.json
    uv run python -m analysis.exp003 RUNS.json PREFIX --dense-break-even 8192
    uv run python -m analysis.exp003 A.json B.json --prefix spec-exit-budget

Reads the runner's JSON, writes `data/exp-003/session-turns.csv` and
`data/exp-003/arm-summary.csv`. Nothing is smoothed and nothing is filled: a
quantity the run did not report stays empty.

A sweep is written one file per cell, so `--prefix` takes several of them and
writes one combined pair of tables: one summary row per file and arm, and
every row carrying the cell it came from — `cell`, `budget_pct`, `idle_s`,
`threshold`, `head`, `append`, read from each file's `meta`. The single-file
form is untouched and its tables keep their exact shape, because the files
they have already produced are referenced by name.

Two columns need their definitions stated rather than inferred.

`canonical_debt_tokens` in the **summary** is
`prompt_tokens - longest_committed_canonical_prefix` with the prefix read from
the arm's **dense probe**, not from its turns. A SpecPrefill turn reports a
usage `cached_tokens` of 0 whatever the cache holds, so a debt computed that
way from the sparse turns would be the prompt length in every arm and would
say nothing about any of them. The per-turn column of the same name is a
different derivation, described under *Which canonical prefix a turn is
credited with* below, and both are written.

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

## Summarizing the catch-up over a session

The summary does not average the per-turn ratios. It pools them: total
canonical gained over total context gained, which for the whole session is
just its endpoints.

    session_catch_up_ratio = (prefix_last - prefix_first)
                             / (prompt_last - prompt_first)

A mean of the per-turn ratios was wrong three ways. It weighted a 200-token
interval the same as a 16,000-token one, and these shapes mix both. It
silently dropped every interval where the prompt did not grow — which is
exactly the idle interval where recovery has its clearest run at the debt, so
the statistic could not see the thing PCSR exists to do. And it blended the
intervals where the recovery job was working with the ones after the exit,
where the prefix advances because the foreground stored its own boundary.

Pooling fixes the first two and keeps the per-turn identity at session scale:

    total debt change = total context growth * (1 - session_catch_up_ratio)

That is why `session_catch_up_ratio` and `final_canonical_debt_tokens` cannot
tell different stories. A reader who finds them disagreeing has found a bug in
this module, not a subtlety.

The third is answered by a second column rather than by narrowing the first.
`recovery_served_catch_up_ratio` pools the same quantity over only the
intervals where `shadow.service_s` advanced, so the recovery job actually got
time. The two answer different questions and the interesting sessions are the
ones where they diverge: the unrestricted ratio is the rate the debt closes at,
which is what the exit depends on, and the restricted one is the recovery
job's own productivity. Both are null, never 0, where they are undefined —
including for an arm that carries no `service_s` counter at all.

## What the exit cost

`spec_exit_turn` on its own does not say what leaving the sparse route bought,
and it can read as a win when it was not one. Splitting the foreground time at
the exit is what shows it:

    pre_exit_foreground_s   wall time over the turns before the exit turn
    post_exit_foreground_s  wall time over the exit turn and everything after
    ttft_at_exit_s          the exit turn's own time to first token

The exit turn can be the most expensive turn of the session, because a dense
prefill of a small tail can cost more than a sparse prefill of the whole
prompt, and a short session may never earn that back. All three are empty when
there is no exit.

`ttft_before_exit_s` is the turn before it, so the step is readable in the
row. The exit turn can cost more than twice the turn it replaced.

**The exit is not a success condition, and no column here says it is.** A
session that leaves the sparse route early is not thereby a better session:
it pays the dense route on every turn after, and in the budget sweep the
uncapped cell exits soonest and is the slowest of the four. `spec_exit_turn`
records when the sparse route was last taken and nothing else.

None of these columns is a verdict either. Whether an exit paid for itself is
a comparison against another arm, which belongs in the findings where the arms
are named and not in a per-arm table.

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

## Which canonical prefix a turn is credited with

`canonical_prefix_tokens` is the longest canonical prefix known to have
existed when the turn ran, and two figures bound it from below.

The recovery job's own counter is a lower bound: it is what one job published,
and it says nothing about state anything else stored. The restored figure —
the admission's `cached_tokens` — is a lower bound too: it is what the lookup
found, not what the store holds.

Taking the job's counter alone was wrong, and wrong in one direction. It is
right only while the recovery job is the thing advancing the prefix. Once the
foreground leaves the sparse route it stores its own boundary, the prefix
grows while the job is not running at all, and the counter freezes: in one
observed run it sat at 20,480 for four turns while the restore was finding
24,576, then 28,672, then 32,768. Every debt, delta and ratio built on it was
overstated for those turns.

So the larger of the two is taken, because the larger of two lower bounds is
the better lower bound, and `canonical_prefix_source` says which one it was.
Neither is taken if it exceeds the turn's own prompt: a prefix of a prompt
cannot be longer than that prompt, and after a compaction the recovery job's
counter still describes the history the session discarded. It is dropped
there rather than clamped, because clamping would invent a bound.
Neither figure is an upper bound and nothing here claims one: the real prefix
may be longer than both, which makes `canonical_debt_tokens` an upper bound on
the debt rather than a measurement of it. `shadow_committed_tokens` keeps its
own column, because the gap between the job's view and the restore's is worth
seeing.

## Which cache count the tail is measured against

`uncached_tail` prefers `route.cached_tokens`, the post-restore count the
admission itself compared, over the `cached_tokens` the usage object reports.
A SpecPrefill turn reports a usage `cached_tokens` of 0 whatever the cache
held, so on exactly the turns this study is about the usage figure understates
reuse and the tail computed from it is the whole prompt.
`cached_tokens_source` says which count was used, and both are written.

The two are also measured at different instants, which is why a reader will
find them disagreeing by exactly one cache block on some turns.
`route.cached_tokens` is what the prefix-cache lookup restored, read at
admission. The usage figure is read when the output is assembled, and the only
write that raises it in between is `omlx/scheduler.py:3863` — the
`_PrefillEvictionNeeded` handler, which adds the tokens the request had
already prefilled before the memory guard paused it, in chunks pinned to a
block boundary. The retry resumes without re-preparing the prefix cache, so
the admission record is never re-taken.

Both are true and neither is stale. The admission figure is the canonical
prefix that existed when the turn *started*; the usage figure is that plus the
turn's own prefill, so it bounds the canonical prefix at the *end* of the turn
— in the budget sweep, each paused turn's usage figure is exactly what the
next turn's lookup then restores. Only the first is a start-of-turn quantity,
which is why only the first feeds `canonical_prefix_tokens` and the debt
derived from it. Taking the larger would credit a turn with canonical state
that turn created.

The gap is not a pause counter. A pause before the first completed block
leaves no gap at all, so it undercounts: one observed run logged 54 pauses
across turns that show eight gaps between them.

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
    "canonical_prefix_tokens", "canonical_prefix_source",
    "canonical_debt_tokens", "uncached_tail_tokens",
    "debt_delta_tokens", "catch_up_ratio", "route",
    "route_source", "route_disagrees", "route_cached_tokens",
    "route_tail_tokens", "cached_tokens_source",
)
DERIVED_TURN_FIELDS = (
    "canonical_prefix_tokens", "canonical_prefix_source",
    "canonical_debt_tokens", "uncached_tail_tokens",
    "debt_delta_tokens", "catch_up_ratio", "route",
    "route_source", "route_disagrees", "route_cached_tokens",
    "route_tail_tokens", "cached_tokens_source",
)
SUMMARY_FIELDS = (
    "arm", "cumulative_foreground_s", "probe_ttft_s",
    "longest_canonical_prefix_tokens", "canonical_debt_tokens",
    "final_prompt_tokens", "shadow_service_s", "shadow_publishes",
    "turns", "spec_exit_turn", "final_canonical_prefix_tokens",
    "final_canonical_debt_tokens", "final_canonical_prefix_source",
    "session_catch_up_ratio", "recovery_served_catch_up_ratio",
    "measured_recovery_share", "dense_break_even_tokens",
    "route_disagreements",
    "pre_exit_foreground_s", "post_exit_foreground_s",
    "ttft_before_exit_s", "ttft_at_exit_s",
)

# What distinguishes one file of a sweep from the others. The runner writes
# these in `meta`, one file per cell.
CELL_FIELDS = ("cell", "budget_pct", "idle_s", "threshold", "head", "append")
CELL_META_FIELDS = ("budget_pct", "idle_s", "threshold", "head", "append")

# Fields only some runs have. They are written when the data carries them and
# left out of the header entirely when it does not: a column that is empty on
# every row of a table tells a reader less than its absence does.
OPTIONAL_CELL_FIELDS = (
    "compact_at_turn", "longest_common_token_prefix", "token_counts_approx",
)
OPTIONAL_TURN_FIELDS = ("post_compact",)

MULTI_TURN_FIELDS = CELL_FIELDS + TURN_FIELDS

# The same columns as the single-file summary plus the cell's identity, in a
# different order. `budget_pct` sits next to `measured_recovery_share`
# because the pair is the reading — the ceiling asked for against the share
# the job received — and the exit's cost sits next to the exit turn for the
# same reason. A test holds the two sets equal so they cannot drift apart.
MULTI_SUMMARY_FIELDS = (
    "cell", "arm", "idle_s", "threshold", "head", "append",
    "budget_pct", "measured_recovery_share",
    "cumulative_foreground_s", "turns",
    "spec_exit_turn", "ttft_before_exit_s", "ttft_at_exit_s",
    "pre_exit_foreground_s", "post_exit_foreground_s",
    "final_canonical_prefix_tokens", "final_canonical_debt_tokens",
    "final_canonical_prefix_source",
    "session_catch_up_ratio", "recovery_served_catch_up_ratio",
    "dense_break_even_tokens", "route_disagreements",
    "probe_ttft_s", "longest_canonical_prefix_tokens",
    "canonical_debt_tokens", "final_prompt_tokens",
    "shadow_service_s", "shadow_publishes",
)


def cell_identity(data: dict, path: pathlib.Path) -> dict:
    """What tells this file's cell apart from the others in a sweep.

    The cell's name is `meta.cell` where the record names itself and the
    file's own stem otherwise, which is how a sweep written one file per cell
    gets a name at all. A field the record does not carry is null, so a sweep
    that varied only the budget does not grow invented values for the rest.
    """
    meta = (data or {}).get("meta") or {}
    identity = {"cell": meta.get("cell") or pathlib.Path(path).stem}
    for field in CELL_META_FIELDS:
        identity[field] = meta.get(field)
    for field in OPTIONAL_CELL_FIELDS:
        if field in meta:
            identity[field] = meta[field]
    return identity


def present_fields(rows: list[dict], optional: tuple) -> tuple:
    """The optional fields any of these rows actually carries, in order."""
    return tuple(field for field in optional
                 if any(field in row for row in rows))


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

    An interval that gained no context leaves the ratio undefined, which is
    None here and an empty cell in the table. Reporting a zero-length interval
    as 0 would say recovery stalled and reporting it as infinite would say it
    won, and the interval says neither. An interval where the context *shrank*
    — a compaction — is undefined for the same reason and not for a different
    one: the denominator is tokens of context gained, and a rate per token
    gained means nothing when none were. The arithmetic would return a
    negative number there, which is not a slower rate of anything.
    """
    if delta_committed_canonical_tokens is None or delta_required_context_tokens is None:
        return None
    if delta_required_context_tokens <= 0:
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
    position = spec_exit_position(routes)
    if position is None:
        return None
    return position if turn_indices is None else list(turn_indices)[position]


def spec_exit_position(routes):
    """Where the exit sits in the turn list, rather than what it is numbered.

    The summary's exit-cost columns split the session at this position, so it
    is computed once here and the turn number is read off it.
    """
    routes = list(routes)
    if not routes or any(route not in KNOWN_ROUTES for route in routes):
        return None
    sparse = [position for position, route in enumerate(routes)
              if route == ROUTE_SPECPREFILL]
    if not sparse:
        return 0
    if sparse[-1] == len(routes) - 1:
        return None
    return sparse[-1] + 1


def recovery_prefix_tokens(row: dict):
    """What the recovery job says it has published, or None.

    Three places can carry it, tried in order of directness: the turn's own
    field, the job's published prefix, and the tokens it has committed. A run
    that reports only `canonical_debt_tokens` still fixes the figure, because
    debt and prefix are one identity there.
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


def restored_prefix_tokens(row: dict):
    """What the serving path restored for this request, or None.

    This is the admission's `cached_tokens`, and it is the same number
    `cached_tokens_for_tail` measures the uncached tail against. It is read
    separately here because the two answer different questions: there, how
    much work the request avoided; here, how much canonical state provably
    existed when it ran.
    """
    value = _recorded(row, "cached_tokens")
    return int(value) if value is not None else None


def canonical_prefix_tokens(row: dict) -> tuple[int | None, str | None]:
    """(tokens, source) — the longest canonical prefix known to have existed.

    Two figures bound it from below and the larger of them is taken.

    The recovery job's counter is a lower bound because it is what one job
    published; it says nothing about state anyone else stored. The restored
    figure is a lower bound because it is what the lookup found, not what the
    store holds. Once the foreground leaves the sparse route it stores its own
    boundary and the prefix grows while the recovery job is not running at
    all, so the job's counter freezes and understates the prefix badly — in
    one observed run it sat at 20,480 for four turns while the restore was
    finding 24,576, then 28,672, then 32,768.

    The larger of two lower bounds is the better lower bound, which is the
    whole of the argument. Neither figure is an upper bound and nothing here
    claims one: the real canonical prefix may be longer than both, and a
    `canonical_debt_tokens` derived from this is an upper bound on the debt.

    `source` is `recovery`, `restore` or `equal`, so a reader can see which
    figure decided it without holding the two columns side by side.

    A figure longer than this turn's prompt is discarded rather than clamped.
    A canonical *prefix* of a prompt cannot be longer than that prompt, so a
    figure that is describes a different token stream — which is exactly what
    the recovery job's counter becomes after a compaction, where it still
    counts the history the session has just thrown away. Clamping it to the
    prompt would invent a bound the data does not support; dropping it leaves
    the other figure to answer, and leaves the column empty if neither can.
    """
    prompt = row.get("prompt_tokens")
    recovery = recovery_prefix_tokens(row)
    restored = restored_prefix_tokens(row)
    if prompt is not None:
        if recovery is not None and recovery > int(prompt):
            recovery = None
        if restored is not None and restored > int(prompt):
            restored = None
    if recovery is None and restored is None:
        return None, None
    if restored is None:
        return recovery, "recovery"
    if recovery is None:
        return restored, "restore"
    if recovery == restored:
        return recovery, "equal"
    return ((recovery, "recovery") if recovery > restored
            else (restored, "restore"))


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
    bounded = [canonical_prefix_tokens(row) for row in rows]
    prefixes = [tokens for tokens, _ in bounded]
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
            "canonical_prefix_source": bounded[position][1],
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


def session_catch_up_ratio(prefixes, prompts):
    """Canonical state gained per token of context gained, over the session.

    The endpoints decide it, which is the point: a turn in the middle that
    reported no prefix costs that turn's own interval and not the session's
    answer. Null when the session did not end longer than it started, because
    a rate per token added is undefined when none were — a session that a
    compaction left shorter than it began has no session-wide rate, only the
    per-interval ones on either side of the discontinuity.
    """
    if len(prefixes) < 2:
        return None
    first, last, start, end = prefixes[0], prefixes[-1], prompts[0], prompts[-1]
    if None in (first, last, start, end):
        return None
    required = int(end) - int(start)
    if required <= 0:
        return None
    return (last - first) / required


def recovery_served_intervals(rows: list[dict]) -> list[int]:
    """The positions i where the recovery job was served over i -> i+1.

    The runtime's `service_s` is cumulative, so the job ran across an interval
    exactly when the counter advanced. An arm carrying no counter — the dense
    and spec arms, and any build without the instrumentation — yields nothing
    here, which is why the ratio built on it is null there and not 0.
    """
    served = []
    for position in range(len(rows) - 1):
        here = (rows[position].get("shadow") or {}).get("service_s")
        after = (rows[position + 1].get("shadow") or {}).get("service_s")
        if here is not None and after is not None and after > here:
            served.append(position)
    return served


def pooled_catch_up_ratio(prefixes, prompts, positions) -> float | None:
    """Total canonical gained over total context gained, across `positions`.

    A ratio of sums, not a mean of ratios: the intervals differ in length by
    two orders of magnitude in these shapes, and weighting them equally would
    let a 200-token turn carry as much of the answer as a 16,000-token one.
    """
    gained = required = counted = 0
    for position in positions:
        nxt = position + 1
        if None in (prefixes[position], prefixes[nxt],
                    prompts[position], prompts[nxt]):
            continue
        gained += prefixes[nxt] - prefixes[position]
        required += int(prompts[nxt]) - int(prompts[position])
        counted += 1
    if not counted or required <= 0:
        return None
    return gained / required


def foreground_split(rows: list[dict], exit_position: int | None):
    """(before, from) the exit turn, in cumulative foreground wall time.

    Both null when there is no exit. A side whose turns did not all report a
    wall time is null too: a sum missing a term reads exactly like a smaller
    total, and there is no way for a reader to tell the two apart.
    """
    if exit_position is None:
        return None, None

    def total(chunk):
        times = [row.get("wall_s") for row in chunk]
        if any(value is None for value in times):
            return None
        return sum(times)

    return total(rows[:exit_position]), total(rows[exit_position:])


def cumulative_foreground_s(payload: dict):
    """The session's foreground wall time, under either name the runner uses.

    Later rounds record it as `session_s`. Both are the same quantity — the
    turns summed, with any probe left out — so both are read rather than
    leaving the column empty for a record that spelled it the other way.
    """
    for key in ("cumulative_foreground_s", "session_s"):
        if payload.get(key) is not None:
            return payload[key]
    return None


def probe_restored_prefix(payload: dict, probe: dict | None):
    """The arm's longest canonical prefix, as the dense probe found it.

    The arm may state it. Otherwise the probe's own admission record is the
    measurement, because asking what the ordinary serving path can restore is
    the only reason the probe is run, and `route.cached_tokens` is that answer.

    The probe's *usage* figure is deliberately not used. A probe long enough
    to be paused by the memory guard reports its own prefill there, which
    would read as canonical state the probe found rather than state the probe
    built — in one observed run a Spec arm's probe restored nothing and still
    reported 36,864 cached tokens by the time it finished.
    """
    if payload.get("longest_canonical_prefix_tokens") is not None:
        return payload["longest_canonical_prefix_tokens"]
    return restored_prefix_tokens(probe) if probe is not None else None


def probe_canonical_debt(payload: dict, probe: dict | None):
    """The arm's canonical debt at the probe: its prompt minus what it found."""
    if payload.get("canonical_debt_tokens") is not None:
        return payload["canonical_debt_tokens"]
    prefix = probe_restored_prefix(payload, probe)
    if probe is None or prefix is None:
        return None
    return canonical_debt(probe.get("prompt_tokens"), prefix)


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

    The two catch-up ratios are pooled rather than averaged, for the reasons
    given in the module docstring. Every interval they are built from is in
    the turns table beside them.

    `route_disagreements` counts the turns where the route the runtime
    recorded and the route this module derives contradict each other, so the
    drift is visible without reading the turns table. It is empty when no turn
    carried both, which is not the same as a session where the two agreed.

    The three exit-cost columns describe what leaving the sparse route cost
    and nothing more. No column here judges whether it was worth it: that is a
    comparison against another arm, and it belongs where the arms are named.
    """
    rows = session_turns(payload)
    derived = derive_turns(rows, dense_break_even)
    indices = [row.get("turn") if row.get("turn") is not None else position
               for position, row in enumerate(rows)]
    prefixes = [entry["canonical_prefix_tokens"] for entry in derived]
    prompts = [row.get("prompt_tokens") for row in rows]
    compared = [entry["route_disagrees"] for entry in derived
                if entry["route_disagrees"] is not None]
    final = derived[-1] if derived else {}

    routes = [entry["route"] for entry in derived]
    exit_position = spec_exit_position(routes)
    before, after = foreground_split(rows, exit_position)
    return {
        "turns": len(rows),
        "spec_exit_turn": spec_exit_turn(routes, indices),
        "final_canonical_prefix_tokens": final.get("canonical_prefix_tokens"),
        "final_canonical_debt_tokens": final.get("canonical_debt_tokens"),
        "final_canonical_prefix_source": final.get("canonical_prefix_source"),
        "session_catch_up_ratio": session_catch_up_ratio(prefixes, prompts),
        "recovery_served_catch_up_ratio": pooled_catch_up_ratio(
            prefixes, prompts, recovery_served_intervals(rows)),
        "cumulative_foreground_s": cumulative_foreground_s(payload),
        "measured_recovery_share": measured_recovery_share(payload),
        "dense_break_even_tokens": dense_break_even,
        # Empty when no turn had both a recorded and a derived route to
        # compare, which is not the same as a session where the two agreed.
        "route_disagreements": sum(compared) if compared else None,
        "pre_exit_foreground_s": before,
        "post_exit_foreground_s": after,
        # The turn before the exit, so the exit's cost is readable in the row
        # rather than by cross-referencing the turns table. Null at an exit on
        # turn 0, where there is no turn before it.
        "ttft_before_exit_s": (rows[exit_position - 1].get("ttft_s")
                               if exit_position else None),
        "ttft_at_exit_s": (rows[exit_position].get("ttft_s")
                           if exit_position is not None else None),
    }


# --------------------------------------------------------------------- CLI


def _parse_args(argv: list[str]) -> tuple[list[str], int | None, str | None]:
    """The positional arguments, plus the two flags.

    argparse is avoided so that `python -m analysis.exp003 RUNS.json` and
    `... RUNS.json PREFIX` keep behaving exactly as the commands already
    recorded elsewhere expect. `--prefix` is what says the positionals are a
    list of files rather than a file and a name, so neither form has to guess
    what the second word meant.
    """
    positional: list[str] = []
    override: int | None = None
    prefix: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--dense-break-even", "--prefix"):
            index += 1
            if index >= len(argv):
                raise ValueError(f"{arg} needs a value")
            if arg == "--prefix":
                prefix = argv[index]
            else:
                override = int(argv[index])
        elif arg.startswith("--dense-break-even="):
            override = int(arg.split("=", 1)[1])
        elif arg.startswith("--prefix="):
            prefix = arg.split("=", 1)[1]
        else:
            positional.append(arg)
        index += 1
    return positional, override, prefix


def _inputs(positional: list[str], prefix: str | None):
    """(paths, prefix) for either calling form.

    With `--prefix`, every positional is a runner JSON. Without it, the old
    form holds: one JSON and an optional round name, and a third positional
    is a mistake rather than a second file, because the old form has no way
    to say which of two words is the name.
    """
    if prefix is not None:
        if not positional:
            raise ValueError("--prefix needs at least one RUNS.json")
        return [pathlib.Path(arg) for arg in positional], prefix
    if len(positional) > 2:
        raise ValueError("several RUNS.json files need --prefix NAME")
    return [pathlib.Path(positional[0])], (positional[1] if len(positional) > 1 else "")


def _turn_rows(arm: str, payload: dict, break_even, extra: dict) -> list[dict]:
    """Every row of one arm's turns table, in the order the session ran.

    The probe keeps its row and takes no derived columns: it is an
    instrument, and a debt delta measured across it would be a property of
    the instrument.
    """
    rows = payload["turns"]
    positions = [i for i, row in enumerate(rows) if row.get("kind") != "dense-probe"]
    derived = dict(zip(positions, derive_turns(
        [rows[i] for i in positions], break_even)))
    out = []
    for position, row in enumerate(rows):
        entry = derived.get(position, {})
        out.append({
            **extra,
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
            "shadow_canonical_debt_tokens": _shadow(row, "canonical_debt_tokens"),
            **{field: _cell(entry.get(field)) for field in DERIVED_TURN_FIELDS},
            # Straight from the row, and only when the row has it.
            **{field: _cell(row[field]) for field in OPTIONAL_TURN_FIELDS
               if field in row},
        })
    return out


def _summary_row(arm: str, payload: dict, break_even, extra: dict) -> dict:
    """One arm's summary row."""
    probe = next(
        (r for r in payload["turns"] if r.get("kind") == "dense-probe"), None
    )
    return {
        **extra,
        "arm": arm,
        "probe_ttft_s": probe.get("ttft_s") if probe else "",
        "longest_canonical_prefix_tokens": _cell(
            probe_restored_prefix(payload, probe)),
        "canonical_debt_tokens": _cell(probe_canonical_debt(payload, probe)),
        "final_prompt_tokens": probe.get("prompt_tokens") if probe else "",
        "shadow_service_s": _shadow(probe, "service_s") if probe else "",
        "shadow_publishes": _shadow(probe, "publishes") if probe else "",
        **{field: _cell(value) for field, value
           in arm_summary(payload, break_even).items()},
    }


def main(argv: list[str]) -> int:
    try:
        positional, override, prefix_flag = _parse_args(argv[1:])
        if not positional:
            raise ValueError(__doc__.strip().splitlines()[2].strip())
        paths, prefix = _inputs(positional, prefix_flag)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    # `--prefix` is the sweep form, however many files it is given: a sweep of
    # one cell still needs the cell's identity on every row, and an arm's
    # threshold is part of that identity even when the arms in a file share
    # it. The positional forms are untouched and keep their exact tables,
    # which is what the files already referenced by name depend on.
    combined = prefix_flag is not None
    turns_out, summary_out = _outputs(prefix)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    turn_rows: list[dict] = []
    summary_rows: list[dict] = []
    for path in paths:
        data = json.loads(path.read_text())
        extra = cell_identity(data, path) if combined else {}
        for arm, payload in data.items():
            if arm == "meta":
                continue
            break_even = dense_break_even_tokens(data, payload, override)
            turn_rows.extend(_turn_rows(arm, payload, break_even, extra))
            summary_rows.append(_summary_row(arm, payload, break_even, extra))

    if combined:
        # The cell's optional fields reach the turn rows too, because
        # `cell_identity` is spread into every one of them.
        turn_fields = (MULTI_TURN_FIELDS
                       + present_fields(turn_rows, OPTIONAL_CELL_FIELDS)
                       + present_fields(turn_rows, OPTIONAL_TURN_FIELDS))
        summary_fields = MULTI_SUMMARY_FIELDS + present_fields(
            summary_rows, OPTIONAL_CELL_FIELDS)
    else:
        turn_fields, summary_fields = TURN_FIELDS, SUMMARY_FIELDS
    _write(turns_out, turn_fields, turn_rows)
    _write(summary_out, summary_fields, summary_rows)
    print(f"wrote {turns_out} and {summary_out}")
    return 0


def _write(path: pathlib.Path, fields: tuple, rows: list[dict]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
