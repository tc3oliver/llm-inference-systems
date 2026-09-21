# Research candidate — SpecPrefill admission economics

**Status: one question, one dataset, no experiment opened.** The measurement
exists and is clean; what it does not contain is the other half of the trade,
and the candidate is recorded rather than promoted for that reason.

It is kept out of EXP-003 deliberately. EXP-003 concluded that progressive
canonical recovery is worth running because it shrinks the suffix the next turn
must prefill, and that forcing the foreground off the sparse route was never
the point — foreground routing is an independent admission decision. This is
that independent decision, and mixing the two would make neither legible.

Dataset: [`data/matched-tail-routing/`](../data/matched-tail-routing/).

## The question

A serving runtime chooses between a dense prefill and a sparse one by a static
threshold on the uncached suffix: above it the request is admitted sparse,
below it dense. On the build measured that threshold is 8,192 tokens.

Nothing in the existing evidence said where that number should be, because no
suffix had ever run both ways. The threshold chose the route, so every dense
sample sat below 8,192 and every sparse sample above it, and a cost curve
fitted across that split is a curve through two disjoint regimes.

## What was measured

Five suffix sizes, each run twice with the route pinned per request and
everything else held: same model, same warm turn, same measured prompt, same
cache state, same greedy output length, a fresh server with the cache directory
removed for every cell.

| uncached suffix | dense | sparse | ratio | throttle pauses, dense / sparse |
|---|---|---|---|---|
| 2,100 | 10.91 s | 4.01 s | 2.7× | 0 / 0 |
| 4,191 | 20.91 s | 5.80 s | 3.6× | 0 / 0 |
| 8,312 | 40.75 s | 9.91 s | 4.1× | 1 / 0 |
| 12,467 | 61.92 s | 14.28 s | 4.3× | 2 / 0 |
| 16,695 | 83.98 s | 18.88 s | 4.5× | 2 / 0 |

The pairing is measured, not assumed: all ten warm turns landed between
103.807 s and 103.866 s, a 59 ms spread across a 104-second operation, and one
cell was independently repeated and agreed to 0.7 ms.

**Sparse was cheaper at every suffix measured**, including the four that sit
below the threshold that routes them dense.

### The memory throttle is not the explanation

An earlier reading of this comparison was withdrawn because every dense sample
in it had been paused by the adaptive prefill throttle and no sparse sample
ever was. That confound is present again at the three largest suffixes and is
now bounded rather than argued about. Dense marginal cost across consecutive
points runs 4.78, 4.81, 5.09, 5.21 ms/token: the pauses bend the curve by about
9% at the top, not by a factor. Priced at the un-throttled rate the largest
dense cell would take 79.8 s instead of 82.85 s, moving its ratio from 4.65 to
4.48.

The throttle accounts for at most about 4% of the gap — and against the
convenient direction, since removing it makes dense look worse relative to
sparse rather than better.

### Where the curves cross

Both are close to straight. From the two unthrottled pairs, dense costs about
4.78 ms/token with no fixed cost; sparse about 0.86 ms/token plus a 1.16 s
fixed charge for scoring and draft setup. They cross at about 340 tokens —
**derived by extrapolation below the measured range**, since the smallest
suffix measured is 2,100, and not claimed as measured. What is measured is that
they do not cross anywhere between 2,100 and 16,695.

The marginal ratio, 18%, sits just under the 24% keep rate the server logged
for the sparse route, which is the mechanism one would expect: sparse cost is
roughly the keep rate times dense cost, plus a fixed scoring charge.

## Why this is a candidate and not a finding

**The dataset measures cost. It does not measure what the threshold is for.**
SpecPrefill drops roughly 80% of conversation tokens and is established as not
output-preserving. A cost crossover at 340 tokens is not a licence to move a
threshold that also has a quality job to do, and no output comparison was
recorded in these runs.

So the shape of the policy is supported — two near-linear curves crossing once
is exactly what a single threshold on suffix size expresses well — while its
constant sits roughly twenty-four times above the cost crossover, and the
reason for that gap is unmeasured.

Two further limits. Suffix size was varied at a single canonical prefix of
24,576 tokens, so any dependence of the crossover on total context is untested.
And one run per cell: enough to establish the geometry, not to state an effect
size.

## What would promote it

An output-quality arm at the same matched suffixes. If sparse and dense agree
on outputs below some suffix size, the threshold is a cost boundary and the
data above already says where it belongs. If they diverge, the threshold is a
quality boundary and the cost curve is the wrong instrument for setting it —
which would itself be the finding, and a more interesting one.
