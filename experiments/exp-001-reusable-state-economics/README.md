# EXP-001 — Reusable State Economics in Interactive LLM Inference

Sparse prefill cut cold time-to-first-token on a 16K prompt from 57.84 s to
19.24 s. I then pointed a real coding agent at the same server and the session
got slower, turn after turn, in a way that got worse the longer the agent ran.

The question this experiment ends up asking is not whether prefill can be made
faster. It can. The question is what a request leaves behind for the requests
that follow it, and whether a latency number taken in isolation can see that at
all. It cannot.

**Primary claim.** The cost of an inference optimization must include the
reusable state it creates, or fails to create, for later requests. The full
statement, and the two terms I introduce to talk about it, are in
[CLAIM.md](CLAIM.md).

## What was measured

Four workloads on one machine: cold single-shot long prompts; a synthetic
interactive workload parameterized by the idle time between turns; five real
coding-agent sessions in three groups, being a paired comparison of three
sessions, one clean request-level trace of a separate session with sparse
prefill enabled and nothing else in the build that could compete to explain
the behaviour, and one session whose cache stayed healthy throughout. Platform, model and controls are in
[METHODOLOGY.md](METHODOLOGY.md).

## What was found

The accelerated path is real in isolation and it does not survive contact with
a continuation-heavy session. In the clean trace the reusable dense checkpoint
climbs from 28,672 to 37,888 tokens over ten requests, collapses back to 28,672
at request 11, and stays pinned there for the remaining ten while the uncached
suffix grows from 17,060 to 33,979 tokens. The scorer that selects tokens for
the sparse path costs 2.7 s at 8,535 tokens scored and 5.7 s at 33,389 — its
overhead grows with the recomputation the session is already paying. That trace
is `figures/fig3-cache-cliff.svg` and `figures/fig4-scorer-cost.svg`, and it is
the strongest evidence here.

Background densification recovers some of it, but only where there is idle time
to run in. At 15 s of think time between turns the synthetic session goes from
108.7 s to 83.1 s. At zero idle it goes from 108.1 s to 119.7 s, 11% slower
than dense.

A separate finding, and not a secondary one: the static prefix boundary used to
protect the system prompt was derived by subtraction and fell short of the real
boundary by as little as 37 tokens once tools were in play. The five questions,
their evidence and their evidence levels are in [FINDINGS.md](FINDINGS.md).

## Where the rest is

- [CLAIM.md](CLAIM.md) — the claim and the two terms introduced here
- [METHODOLOGY.md](METHODOLOGY.md) — platform, model, workloads, controls
- [FINDINGS.md](FINDINGS.md) — five questions, each with evidence
- [LIMITATIONS.md](LIMITATIONS.md) — single runs, differing trajectories, and
  the two claims cut for lack of a source
- [../../docs/negative-results.md](../../docs/negative-results.md) — the four
  hypotheses this study refuted
- `data/exp-001/` — every number behind every figure
- `figures/plot.py` — regenerates all nine figures from those CSVs

Upstream consequences, both open pull requests at the time of writing: oMLX PR
[#3756](https://github.com/jundot/omlx/pull/3756) (prefix boundary correctness)
and PR [#3762](https://github.com/jundot/omlx/pull/3762) (per-request sparse
prefill control on the Anthropic messages endpoint, no default changed). The
runtime built along the way is in [../../ENGINEERING.md](../../ENGINEERING.md).
