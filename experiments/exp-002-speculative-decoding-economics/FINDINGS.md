# EXP-002 — Findings

Every claim below carries an evidence label. **Observed** is something the
runtime reported. **Measured** is a number from a matched pair. **Derived** is
arithmetic over measured numbers. **Inferred** is a mechanism that the measured
numbers are consistent with and that no control here isolates. **Not
established** is what the sentence before it might have tempted you to conclude.

All matched comparisons are in `data/exp-002/matched-comparisons.csv`, all 36
runs in `data/exp-002/runs.csv`.

## 1. Fixed depth-3 speculation is a loss in most of the cells it was measured in

**Measured.** Matched decode speedup against dense, model A, fixed draft depth 3,
two repeats per cell:

| cell | acceptance | tokens/cycle | dense ms/tok | fixed ms/tok | decode speedup |
|---|---:|---:|---:|---:|---:|
| code / short | 55.9% | 2.10 | 9.79 | 10.89 | **0.899x** |
| code / long | 54.5% | 2.07 | 10.78 | 12.41 | **0.869x** |
| prose / short | 25.1% | 1.33 | 9.72 | 17.31 | **0.562x** |
| prose / long | 26.9% | 1.37 | 10.74 | 18.83 | **0.571x** |
| copy / short | 64.3% | 2.44 | 9.68 | 9.32 | **1.039x** |

Within-arm spread across the two repeats is 0.1% to 3.3%, so every one of these
directions is far outside the noise.

**Derived.** Speculation at a fixed depth of 3 made the decode phase 10–13%
slower on generated code and 43–44% slower on generated prose, on a model whose
observational acceptance on real agent traffic has a median of 88.6%.

**Not established.** That depth 3 is a bad setting in general. It is the
runtime's own default maximum, and under the adaptive controller it is a ceiling
rather than a count; §3 is what happens when the controller is allowed to move.

## 2. Cycle cost, not acceptance, decides profitability

The acceptance rate is a per-cycle probability. What it is worth depends on what
a cycle costs, and the runtime measures that.

**Measured.** One verify cycle at depth 3 costs, in the runtime's own backbone
timer, 21.1–23.5 ms on model A against a dense decode step of 9.7–10.8 ms. The
same cycle on model B costs 51.6–53.0 ms against a dense step of 39.2 ms.

**Derived.** The cost ratio — one cycle priced in dense steps — is 2.42–2.46 on
model A and 1.37 on model B. A cycle emits `accepted + 1` tokens, so speculation
pays on the speculated part of the output exactly when tokens per cycle exceeds
the cost ratio. On model A that threshold is about 2.43 tokens per cycle, which
is 71% acceptance at depth 3. On model B it is 1.37, which about 12% acceptance
already clears.

**Derived.** Applying that to the speculated share of each completion predicts
the matched decode speedup across the whole measured range, 0.56x to 1.81x:

| cell | policy | predicted | measured |
|---|---|---:|---:|
| A code / short | fixed 3 | 0.867 | 0.899 |
| A code / long | fixed 3 | 0.852 | 0.869 |
| A prose / short | fixed 3 | 0.551 | 0.562 |
| A prose / long | fixed 3 | 0.566 | 0.571 |
| A copy / short | fixed 3 | 0.994 | 1.039 |
| A code / short | adaptive | 0.965 | 0.999 |
| A code / long | adaptive | 1.005 | 1.028 |
| A copy / short | adaptive | 1.111 | 1.169 |
| A prose / short | adaptive | 0.893 | 0.899 |
| A prose / long | adaptive | 0.983 | 0.915 |
| B code / long | adaptive | 1.825 | 1.812 |

Ten of the eleven are within 5%. The prediction is systematically a little low,
which is expected: the component timers account for the speculative work and not
for the rest of the decode loop, so the dense baseline they are compared against
carries overhead the speculative estimate does not.

The one cell where the prediction is high rather than low — model A, prose,
long, adaptive, predicted 0.983 against a measured 0.915 — is the cell where the
controller parked the sequence after six or seven cycles. The model assumes the
parked remainder runs at exactly the dense rate. It does not.

**Inferred.** The cost ratio differs between the two models because of what a
multi-position forward costs each architecture, not because of anything about
the content. A dense 27B decoding one token is bound by reading its weights, and
reading them once for four positions instead of one costs 37% more. A
mixture-of-experts model with 3B active parameters is already cheap per token,
and widening the forward to four positions routes more experts and leaves the
single-token decode kernels, so the same widening costs 142% more.

**Not established.** That expert routing is the mechanism. The measurement here
is the end-to-end cost of the verify forward, from one timer inside the runtime.
Attributing it to routing rather than to kernel shape would need a per-kernel
trace, which was not run, because the choice between those two explanations does
not change any decision in this experiment.

## 3. The adaptive controller avoids every losing region it was shown

**Observed.** In the prose cells the controller parked the sequence back onto the
standard decoder. On the long prose prompt it ran 6 and 7 verify cycles out of
256 tokens and then stopped speculating: 2.3% and 3.1% of the completion was
produced speculatively. On the short prose prompt the two repeats parked at
different points, 59% and 15% of the completion speculated, and that disagreement
is the controller reacting to its own rolling estimates rather than measurement
noise. Three parked depth-0 cycles are reported in every MTP run, which is the
controller's standing probe.

**Measured.** What that buys, as matched decode speedup against dense:

| cell | fixed depth 3 | adaptive |
|---|---:|---:|
| code / short | 0.899x | 0.999x |
| code / long | 0.869x | 1.028x |
| prose / short | 0.562x | 0.899x |
| prose / long | 0.571x | 0.915x |
| copy / short | 1.039x | **1.169x** |

**Derived.** The controller converts a 43% loss into a 9% one and a 13% loss into
parity. It does not fully recover dense performance on prose: parking is not
free, and a sequence that has been parked has already paid for the cycles that
taught the controller to park it.

**Measured.** In the one cell where fixed depth 3 wins, the controller wins by
more: 1.169x against 1.039x. The depth counters say why. Fixed depth 3 drafted
three tokens every cycle, 81 cycles, 2.44 tokens per cycle at a cost ratio of
2.46. The controller drafted mostly two, 89–95 cycles, 2.15 tokens per cycle at a
cost ratio of 1.94. It gave up throughput per cycle to buy a cheaper cycle, and
came out 12% ahead.

This is the case that would otherwise have been mistaken for a controller
overhead problem. Fixed depth beating adaptive on tokens per cycle while losing
on wall clock is the signature of a controller that is optimizing the right
quantity.

## 4. A faster decode phase is not a faster request

**Measured.** Enabling the mechanism costs time before the first token. Median
time to first token, dense against adaptive: 9.30 s against 9.95 s on the long
model A prompts, 1.89 s against 2.29 s on the copy prompt, and 54.55 s against
55.75 s of server-reported prefill on model B.

**Inferred.** This is the prefill-time priming pass the runtime runs to fold
hidden states into the draft head so the first draft is not made from a cold
head. Its cost is paid inside prefill and is not separately timed, so the
attribution is by elimination — it is the only thing the mechanism adds before
the first token — rather than by measurement.

**Derived.** With the prefix cache cleared before every request and a 256-token
output budget, that cost is larger than the decode saving in every model A cell.
The copy cell is the clearest: the decode phase got 1.169x faster, saving 0.28 s,
and the request got slower, because the first token arrived 0.39 s later. End-to-
end speedup in that cell is 0.970x.

On model B the same arithmetic runs the other way: 1.2 s more before the first
token, 4.5 s less spent decoding, 1.053x end to end.

**Not established.** That this generalizes to an interactive workload. Every run
here is a cold prefill by construction, which is the geometry most hostile to a
mechanism that charges at prefill and pays at decode. A continuation-heavy agent
turn arrives against a mostly warm prefix, where prefill is a small fraction of
the request and the decode result dominates. Which way that lands was not
measured, and this experiment does not claim it.

## 5. Correctness: the mechanism costs reproducibility, as the runtime says it will

**Measured.** On model B, where the requests were genuinely greedy, dense
decoding is bit-reproducible: two runs of the same prompt produced identical
1,052-character completions. With the mechanism on, the same prompt produced two
different completions, diverging from each other at character 256, and both
diverging from the dense completion at character 235.

**Observed.** The runtime documents a cause. At draft depth 2 or more the verify
forward routes through a different set of quantized matmul kernels than the
single-token path, whose numerics can differ at the bottom of the mantissa, and
the source says in as many words that greedy output identity between the two is
not guaranteed at depth 2 or above.

**Derived.** So this is a documented divergence rather than a defect, and the
performance numbers in §1 to §4 stand. What it removes is the strong form of the
correctness check: these runs cannot demonstrate that the mechanism preserves the
model's output, only that it changes it by about as much as the runtime warned it
would.

**Measured, and the reason the check is weak on model A.** Model A did not run
greedy — see METHODOLOGY §Sampling — and under stochastic sampling its dense arm
is not reproducible either: two dense runs of the same prompt with the same seed
diverged after 4 to 993 characters depending on the cell. Where both arms happen
to be reproducible, in the copy cells, dense and speculative produced identical
completions. That is one cell, and it is the only positive correctness evidence
here.

**Not established.** Whether the divergence in §5 ever reaches a semantic
difference. Nothing here measured output quality, and this experiment does not
go looking: a divergence measured on one prompt on one model is a data point for
the question, not the question's answer. The question itself — whether an
optimization's numerical difference reaches the output — is the subject of
[the correctness thread](../../research-threads/inference-correctness.md), whose
list of missing work named speculative decoding explicitly. What EXP-002 adds to
that list, and what it leaves on it, is recorded there rather than here.

To state the three facts without the interpretation attached:

- Dense greedy generation was bitwise reproducible: two runs, identical bytes.
- MTP generation diverged from dense, and also diverged between its own two
  repeats of the same prompt.
- The runtime's verify forward routes through the verify-shape quantized matmul
  kernels at draft depth 2 and above, a path whose numerics the source says are
  not bit-identical to the single-token path. That is the execution path the
  divergence is associated with. It is the documented candidate, not a cause
  this experiment isolated: no run here varied the kernel path with everything
  else held.

## 6. The production consequence, and why there is no upstream change

**Observed.** The instance this machine actually serves runs model B with the
mechanism enabled, at the runtime's default maximum draft depth, through the
adaptive controller. Seventeen finished sequences read from its own log during
this work, `data/exp-002/production-mtp-sequences.csv`, have a median acceptance
of 80.0%, a median 2.38 tokens per cycle, and a median cycle cost of 59.6 ms.

**Derived.** Against the 39.2 ms dense step measured for that model in §2, those
sequences sit at a cost ratio of 1.52 against 2.38 tokens per cycle — comfortably
inside the profitable region, and consistent with the 1.81x measured directly.

**Measured.** On a matched 13,659-token coding prompt, model B decodes at
21.63 ms per token with the controller and 39.20 ms per token without it.

So the production decision is to change nothing. The mechanism is on, it should
stay on, the controller is the right policy for it, and the default depth
ceiling is not the thing to tune.

No upstream change is proposed either. The gate for proposing one was a
reproduced regression with an identified controller misdecision behind it. The
regressions in §1 are real and the controller is what prevents them; it chose
correctly in every cell, including the one where choosing a shallower depth than
the configured maximum was the winning move. A controller that is already right
does not need a patch, and that is a result rather than a missing deliverable.

One observation is worth recording without acting on it: a parked sequence on
model A still finishes about 8% behind dense (§3). Parking stops the bleeding
and does not undo it. Whether the re-entry probe schedule costs more than it
recovers on a workload that never becomes profitable is a question this
experiment raises and does not answer.

## 7. What replaced the question the thread was asking

The [research thread](../../research-threads/speculative-decoding.md) asked when
speculative decoding reduces the time an interactive workload takes, and could
not answer it because it had 431 acceptance rates and no matched arm. It also
carried a finding that acceptance is a property of the model rather than of the
task.

Both survive, and the second one turns out to have been pointing at this. If
acceptance is fixed by which draft head shipped with the model, then the thing
that varies between deployments is the other term. The cost of a verify cycle is
also a property of the model — of its architecture rather than its head — and it
is the term that decided every cell here. Two models of a similar size class,
the same runtime, the same code prompt: one loses at 56% acceptance, the other
wins 1.81x at 79%.

The practical form of that: before enabling this mechanism, measure one dense
decode step and one verify cycle. The runtime reports both. Their ratio tells you
the acceptance you need, and it is a number you can get from two requests.
