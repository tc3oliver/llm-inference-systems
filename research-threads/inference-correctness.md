# Research thread — correctness as a constraint on optimization

**Status: research thread, not a completed experiment.** Five pieces of
evidence point at one idea, and the idea is worth stating, but they were
collected for five different purposes and none of them was designed to test it.
There is still no systematic correctness sweep in this repository. The fourth
case arrived as a by-product of EXP-002 and is recorded here because that study
stopped at measuring the divergence rather than judging it. The fifth arrived
from taking a mechanism into a production workload, and it is the only one so
far where the defect is in how a *successful* optimization was read rather than
in what it computed.

## The idea the evidence points at

An inference optimization is only allowed to change how fast the answer
arrives. Every optimization here changes the arithmetic that produces it, so
for each one the question is whether the difference reaches the output. The
five cases below answer that question five different ways, and the useful
part is that the answers are not the same.

They also suggest where to look. Each failure or near-failure appeared at a
boundary: a block boundary in the cache, a chunk boundary in attention
routing, a role boundary in the prompt template, a layer boundary between a
recurrent cache and an attention one. Optimizations are built
around boundaries, because a boundary is where work can be divided, and a
boundary is therefore where the division can be wrong.

## Case 1 — restoring a cached prefix does not change the output

`data/correctness/cache-restore-output-identity.csv`, September 2026. Seven
prompts, each issued twice against the same server: once in a state where
little or none of it was cached, and once when the prefix cache could serve
most of it. Prompts were synthetic records, generated for the test. Output
was hashed and compared.

| Case | Pass 1 | Pass 2 | Cached tokens 1 → 2 | Output |
|---|---:|---:|---|---|
| short | 5.985 s | 1.560 s | 0 → 1,024 | identical |
| short tail A | 1.472 s | 1.447 s | 1,024 → 1,024 | identical |
| short tail B | 1.442 s | 1.447 s | 1,024 → 1,024 | identical |
| long | 56.312 s | 2.314 s | 1,024 → 13,824 | identical |
| long tail A | 2.879 s | 2.312 s | 13,824 → 13,824 | identical |
| long tail B | 2.422 s | 2.420 s | 13,824 → 13,824 | identical |
| long reasoning | 2.803 s | 2.805 s | 13,824 → 13,824 | identical |

Seven of seven identical. The long case went from 56.3 s to 2.3 s, a factor
of twenty-four, and produced the same bytes.

This is the optimization that reuses state, and on this evidence it is exactly
neutral on semantics. That is the property EXP-001 relies on when it treats a
restored checkpoint as free: the cache is not an approximation, it is
memoisation, and the only thing it can cost you is the recomputation you were
going to do anyway. Fourteen runs is not a proof, and the test does not cover
quantized cache entries or the recurrent-state path.

## Case 2 — changing the attention route changes the logits and not the answer

`data/correctness/attention-route-logit-divergence.csv`, September 2026.
Thirteen runs of one 68,034-token prompt across three builds that differ in
how the attention kernel is routed at long sequence lengths. The independent
variable is how many of the 256 routing decisions took the bounded path: zero
in one build, 144 in another, all 256 in the third. The prompt is a real
captured agent prompt and is not published; only its length and the hashes
below leave the machine.

| Quantity | Result |
|---|---|
| Distinct logit-vector hashes | 3 — one per build |
| Top-3 logit values | differ between builds by up to about 0.4 |
| Distinct argmax tokens | 1 |
| Distinct top-3 token sets | 1 |
| Distinct output hashes | **1** |

Three implementations that are not numerically identical, at 68K context,
produced the same sampled output on every run. Within a build the logit hash
was stable across repeats, so each build is deterministic; the difference is
between builds, not between runs.

The methodological point is the one worth keeping. A logit-level comparison
would have flagged all three builds as divergent and told you nothing about
whether any of them was wrong. An output-level comparison found them
equivalent for this prompt. Neither check subsumes the other: the logit hash
detects a change the output hash cannot see, and only the output hash speaks
to what a user would receive. A correctness harness that reports one of them
is reporting half the question.

The limit is obvious and should be stated: one prompt. The margin between the
top two logits on this prompt was wide enough to absorb a 0.4 perturbation.
A prompt where the top two candidates sit close together is exactly where
this would break, and no such prompt was tested.

## Case 3 — the optimization that did change what the model saw

This one is written up in full in EXP-001, and is summarised here because it
is the case where the answer came out the other way.

Sparse prefill must not discard the system prompt and tool definitions, so
the implementation protected a prefix of the prompt. The boundary of that
protected region was derived by subtracting a re-render of the non-system
messages from a render of the whole prompt, which assumes a chat template
emits the same text regardless of which roles are present. The template in
use does not. With tools in play the derived boundary fell as little as 37
tokens short, which placed the end of the tool instructions and the start of
the operator's system prompt inside the region the optimization was free to
drop.

The fix measures the boundary rather than inferring it: render the static
messages twice with two different throwaway turns and keep the token prefix
both renders share with the real prompt. Tokens that two probes agree on
cannot depend on conversation content. It was submitted upstream as
[oMLX PR #3756](https://github.com/jundot/omlx/pull/3756), which is open at
the time of writing.

Evidence and the shortfall figure:
[`experiments/exp-001-reusable-state-economics/FINDINGS.md`](../experiments/exp-001-reusable-state-economics/FINDINGS.md).

## Case 4 — speculative decoding changes the output, and reproducibility with it

This one comes out of
[EXP-002](../experiments/exp-002-speculative-decoding-economics/), which went
looking for latency and found this on the way. It is recorded here rather than
there because it belongs to this thread's question, and because EXP-002
deliberately stopped at the measurement.

The model is a dense 27B at 4-bit, the only arm in that study where the requests
were genuinely greedy. One 13,659-token prompt, 256 output tokens, prefix caches
cleared, two runs per policy, nothing varying but whether the mechanism was on.

| Arm | Run-to-run | Against dense |
|---|---|---|
| dense | identical, 1,052 of 1,052 characters | — |
| speculative | **not** identical — the two runs diverge at character 256 | diverges at character 235 |

So the direction of this case is the opposite of Case 1. Restoring a cached
prefix is memoisation and was exact seven times out of seven. Speculative
decoding is not memoisation: it changes which kernels compute the logits, and on
this model the output changed and stopped being reproducible at all.

The runtime documents a candidate. Its verify forward covers the drafted
positions in one pass, and at draft depth two and above that pass routes through
the verify-shape quantized matmul kernels rather than the single-token decode
path; the source states in as many words that greedy output identity between the
two is not bit-guaranteed there. That is the execution path this divergence is
associated with. It is not a cause anybody isolated: no run varied the kernel
path with everything else held, and doing so would need a build that can force
one shape, which does not exist here.

What this does **not** say is that speculative decoding is wrong. The acceptance
rule it implements is exact by construction — under greedy sampling it accepts a
draft only on an exact argmax match — so a divergence has to come from the
arithmetic underneath rather than from the algorithm above. A different
floating-point path reaching a different token at a near-tie is the expected
shape of that, and it is also exactly what Case 2 found for attention routing,
where three numerically different builds still produced one output. Here the
output did change. Whether either outcome matters to a reader is the measurement
neither case has.

That is the gap this thread keeps naming: an output-level comparison tells you
the bytes differ, and nothing tells you whether the answer got worse. Case 4
adds a second optimization to the list of ones where that question is now
specific rather than hypothetical.

## Case 5 — the cache restore succeeded and was read as empty

This one has no measured output comparison and is here anyway, because it is
the cleanest example in the thread of a correctness defect that no performance
number and no cache hit rate can see.

SpecPrefill scores a prompt with a draft model to choose which tokens the target
must compute densely. That scoring pass reads its own restored prefix cache,
and it took the cache's logical position from `cache[0].offset`, behind a
`hasattr` guard. Layer 0 of a hybrid model is recurrent and carries no logical
offset, so the guard answered **0** rather than failing. The chain that follows
is the whole point:

    the restore succeeds at the storage layer
    -> the consumer reads its position as 0
    -> the full prompt is prefilled on top of the state it already held
    -> importance is computed over a key range other than the one it assumes
    -> the selected token set is computed from it

Nothing in that chain reports an error, and the storage layer's own metric — a
cache hit — says the restore worked, because it did. What failed is the
agreement between two components about what position the restored state
represents.

The fix derives the position from the model's attention layers through the
existing layer-to-cache mapping, cross-checks every position-bearing entry
against the others, and raises rather than guessing when none can settle it. It
was submitted as
[oMLX PR #3840](https://github.com/jundot/omlx/pull/3840), open at the time of
writing. Regression coverage establishes that cold and warm scoring now agree on
both the importance vector and the selected token set.

**What is established and what is not.** The mechanism and the fix are
source-established and reproduced — 19 tests, 16 of which fail against the
unfixed source. That the selection was wrong in a way that reached a served
answer is **not established**: on the affected models the cache never produced a
hit at all until a second change made the path reachable, so the defect's
practical exposure and its correctness cost are two different questions and only
the first is answered. The rest of the mechanism is in
[hybrid draft prefix reuse in SpecPrefill](specprefill-draft-cache-reuse.md).

The generalizable form, which is why it belongs in this thread:

> Restoring state is not enough. Every consumer of that state must agree on
> what position it represents, and a storage-layer hit is not evidence that
> they do.

## What these five cases do and do not establish

They establish that the five optimizations sit in different places on the
semantic-risk axis, and that the placement is not guessable from how
aggressive the optimization sounds. Reusing a cached prefix sounds risky and
is exact. Rerouting an attention kernel sounds like an implementation detail
and perturbs every logit without changing the answer. Protecting a prefix sounds
like bookkeeping and was the one that silently changed the model's input.
Speculative decoding sounds like the most dangerous of the five, because it
guesses — and its guessing is the exactly-correct part, while its arithmetic is
what moved the output. Reading back a cache sounds like the safest thing on the
list, and Case 5 is a defect in exactly that, on the reading rather than on the
cache.

They do not establish a correctness boundary as a function of context length,
which is the interesting version of the question. That would need the same
prompt family swept across lengths against a fixed reference, with a decided
threshold, and it does not exist here. Specifically missing:

- No comparison of quantized against unquantized key-value cache output.
- Speculative decoding on and off is now **partly** measured — see Case 4. What
  is missing there is no longer the comparison but the judgement: the outputs
  differ and nothing says whether the difference matters.
- No structured-output or tool-call validity measurement under any
  optimization.
- No reference arm at higher precision. On this machine the model does not
  fit unquantized at long context, so any reference is itself quantized, and
  a study would have to say so as a limitation rather than solve it.

## Provenance and what stays private

The case 1 prompts were synthetic and generated for the test. The case 2
prompt is a real captured agent prompt held outside this repository; it is
not published, and neither is any model output text from it. What is
published is its token count and the hashes, which reveal nothing about its
content.

The local benchmark archive these cases were drawn from contains captured
vendor system prompts. That is why it is not published and why only derived
timings, token counts and hashes appear here. The rule is in
[`DATA_POLICY.md`](../DATA_POLICY.md).
