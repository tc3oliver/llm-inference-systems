# Canonical-state publication and foreground adoption as separate control planes

*Research thread; one matched pair. Not a change to #3793.*

Background canonical-state recovery (PCSR, #3793) publishes each safe cache
block as soon as it is reached. Every publication a foreground request adopts
moves the SpecPrefill draft scoring-window origin, and a moved origin is a cold
draft rescore that is
[correct under the current scoring contract](specprefill-scoring-window-origin.md).
This page asks whether adopting every newly published block is too eager.
The evidence ladder is [`EVIDENCE.md`](../EVIDENCE.md); the data is
[`data/pcsr-foreground-adoption-grain/`](../data/pcsr-foreground-adoption-grain/).

---

## Question

The question first asked was whether PCSR should publish in larger steps. It
became this:

> Is exposing every newly recovered canonical block to foreground requests too
> eager?

## Why it is not a question about publication

Publishing only every N blocks is **incompatible with the current disposable
live-state lifecycle** under the production budget and slice. **Source-established:**

- the scheduler retires the recovery job's live dense state whenever foreground
  work appears, and whenever the budget window is spent;
- anything processed past the last published boundary is discarded, and the
  next start restores the published prefix and re-reads from there.

At 10% of a 30 s window, one live state gets about 3 s of service. In both arms
below, the recovery job's processed count never ran more than 512 tokens (one
slice) past its committed count (`pcsr_processed − pcsr_committed`, maximum
512). **Observed.**

A policy that publishes every four blocks therefore never reaches its next
boundary inside one state's life, publishes nothing new, and lets the
frontier regress. Flushing before retirement instead makes it behave exactly
like publishing every block. This is a design constraint, not an experiment
result, and nothing was run to show it.

What the draft actually responds to is the frontier a foreground request
*adopts*. So the instrument leaves publication alone, and rounds only the
adopted frontier down to a multiple of N blocks.

## The pair

The pair is `visibility-grain-ab.csv`, 17 requests per arm on a research build
of the production stack, with production stopped and nothing else on the
machine.

- **Held fixed:** recovery work, durable publication at every block, the
  budget, the slice, the draft cache, the workload and the request timing.
- **Varied:** A adopts every published block (today's behaviour); B adopts only
  in 4-block steps.

The workload grew from 36,027 to 44,208 prompt tokens. Totals are over turns
1–16; turn 0 is cold and identical in both arms.

| | A — adopt every block | B — adopt every 4 blocks |
|---|---|---|
| publications / durable frontier at the last request | 11 / 11,264 | 12 / 12,288 |
| visible frontier changes | 11 | 3 |
| draft cold rescores | 11 | 3 |
| draft scoring, total | 63.8 s | 22.4 s |
| target sparse prefill, total | 459.1 s | 456.5 s |
| foreground cost (draft + target) | **522.9 s** | **478.9 s** |
| client TTFT, total | 557.9 s | 490.6 s |
| mean adopted frontier | 6,464 | 5,632 |

- **Measured:** durable progress did not regress in B. At the last request
  A had published 11 blocks and B 12. The difference is timing: A's twelfth
  landed after that request was sent, and the server log records both jobs
  ending at 12,288 committed tokens. In both arms the processed-to-committed
  gap was never more than one slice.
- **Measured:** every change in the adopted frontier was a draft cold rescore,
  and no rescore happened without one. That was 11 of 11 in A and 3 of 3 in B.
  A cold rescore took 4.9–6.2 s and a hit 0.3–1.2 s.
- **Derived:** foreground cost fell by 44.0 s, 8.4%. Almost all of that is
  draft scoring, 41.4 s.
- **Derived**, without A's turn 5 (below) or the matching B turn: 488.3 s
  against 450.3 s, 7.8% lower. Target prefill is then 3.1 s *higher* in B.
  That is the expected price of adopting on average 832 fewer cached tokens,
  and it is an order of magnitude smaller than the draft time saved.

## The finding

> Canonical-state publication and foreground adoption should be separate
> control planes.

Publication decides when reusable state becomes durable. It is bounded by the
recovery lifecycle and should stay at every safe block. Adoption decides when a
foreground request switches onto that state. Here, switching on every block
cost a draft rescore each time, and bought a few tenths of a second of
target prefill per block. **Measured** for this pair; the mechanism (the draft window
origin follows the adopted frontier) is **source-established** on the
[scoring-window page](specprefill-scoring-window-origin.md).

**Not established:**

- that 4 blocks is the right grain;
- that a fixed grain is better than a hysteresis or cost-based adoption rule;
- that the gain holds at other prompt sizes, recovery rates or request rates.

The trade depends on the ratio of recovery progress to request rate. Here that
was about three-quarters of a block per turn.

## Limitations

- One pair, 16 compared turns per arm. The arms ran one after the other (B,
  then A) in the same window rather than interleaved.
- One workload shape: 36K–44K tokens, about 512 tokens per turn, a 30 s gap and
  no system prompt. At the 67K sizes seen in agent sessions, a cold rescore
  costs more.
- A's turn 5 took 54.5 s to first token, with a 33.7 s sparse prefill and no
  frontier change. The log shows no recovery yield or warning around it, and
  it is unexplained. Totals are given with and without it, and the direction
  holds either way.
- The neural-engine prefill path was unavailable to the research process, so
  target prefill ran on the GPU in both arms. Production routes part of it to
  the neural engine, which would make target prefill cheaper and a draft
  rescore relatively more expensive. That is not measured.
- TTFT includes the prefix restore, which is also smaller in B. The
  foreground-cost column excludes it.

## What would take it further

This doesn't lead to a change to #3793. The next step is an adoption-policy
experiment over several prompt sizes and request rates. It would compare a
fixed grain, a hysteresis rule, and a rule that adopts only when the target
saving exceeds the expected draft rescore. After that comes a decision about
whether adoption belongs in PCSR, in the prefix-cache restore path, or in
SpecPrefill admission.

## Where this connects

It follows [the moving scoring-window origin](specprefill-scoring-window-origin.md),
which established that the rescore is correct and cannot be removed by
changing the draft cache key. This page leaves the rescore alone and asks how
often a request should trigger it. The recovery lifecycle constraint is the one
[background work under foreground QoS](background-work-under-foreground-qos.md)
built on purpose: state that can be taken back whenever foreground work
arrives.
