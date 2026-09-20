# Research harness

> **Status.** This is measurement tooling, not a result. It was built for a
> planned multi-experiment program that was then cut back deliberately. It has
> since produced two things: the exploratory baseline in
> `data/exploratory/native-mtp-dense-baseline.jsonl`, and every run behind
> [EXP-002](../experiments/exp-002-speculative-decoding-economics/), whose raw
> records are in `data/exp-002/raw/`. EXP-001 does not depend on it. It is
> committed because it is the reproduction path for measuring this runtime, and
> because the isolation rules it encodes are the ones I actually use.

Measures one local inference server, writes one JSON line per run, and keeps the
grid resumable. Everything it needs to find on the machine comes from an
environment variable or a flag, so nothing about a particular machine is
committed here.

## What is recorded

Token counts, timings, the server's own usage keys and the server's per-sequence
acceptance log line. **No prompt text and no completion text is ever written to
disk.** The runner holds the generated prompt in memory, sends it, and records
its length. The tests assert this.

Run records are validated against `schemas/run.schema.json` before they are
written. Every metric is nullable, and a metric that was not measured is `null`.
It is never `0`, because a zero and an absence mean different things and the
difference is exactly what an analysis has to see.

## Environment

| Variable | Meaning | Default |
| --- | --- | --- |
| `OMLX_BIN` | inference server binary | required to start a server |
| `OMLX_SRC` | server source checkout, for the commit sha | two levels above the binary's directory |
| `OMLX_MODEL_DIR` | directory holding model weights | required to start a server |
| `OMLX_RESEARCH_BASE` | base path: settings, logs, cache | required |
| `OMLX_HOST` | bind and connect host | `127.0.0.1` |
| `OMLX_PORT` | port | `8011` |
| `OMLX_MEMORY_GUARD_GB` | memory guard | `36` |
| `OMLX_SSD_CACHE_DIR` | paged SSD cache directory | `<base>/ssd-cache` |
| `OMLX_API_KEY` | bearer token, if the server requires one | unset |
| `MODEL` | model id to serve and measure | required |
| `OMLX_MIN_FREE_PCT` | preflight system-wide free-memory floor, percent | `60` |
| `OMLX_MAX_WIRED_GB` | preflight wired-plus-compressor ceiling | `24` |
| `OMLX_MAX_FOREIGN_RSS_GB` | preflight foreign-process ceiling | `20` |

A research instance runs on its own port with its own base path. It is not the
same process as any long-running server on the machine, and the harness will not
touch one it did not start.

## Safety preflight

`harness.server` refuses to start a server when any of these hold, prints what it
measured, prints the reason and exits non-zero:

- the system-wide free memory percentage from `memory_pressure` is under
  `OMLX_MIN_FREE_PCT`;
- wired pages plus pages occupied by the compressor, from `vm_stat`, exceed
  `OMLX_MAX_WIRED_GB`;
- another server process is already resident above `OMLX_MAX_FOREIGN_RSS_GB`;
- the configured port is already accepting connections.

### Why those two memory gates

An earlier version summed free, inactive and speculative pages and demanded
40 GB. That gate refused starts that were in fact safe. On this machine roughly
28 GB sits in **active** pages that no process holds as resident memory: it is
file-backed page cache, which the kernel evicts the moment something needs the
space. Counting it as unavailable makes a machine with a warm page cache look
full. The wired figure at the same moment was 2.7 GB.

So the gates ask the two questions that actually matter. `memory_pressure`
reports the system's own view of how much memory is free, which already accounts
for what is reclaimable. Wired plus compressor is the part that is genuinely not
reclaimable, and is what a new model cannot displace. Active pages are
deliberately not counted.

`stop` kills only the process id the harness itself recorded in
`$OMLX_RESEARCH_BASE/research-instance.json`, and only when that file says the
harness started it. It will not stop a server someone else is using.

## Running

```sh
uv sync --extra dev --extra tokenizer

uv run python -m harness.server init-base
uv run python -m harness.server preflight
uv run python -m harness.server start
uv run python -m harness.server start --env OMLX_MTP_FIXED_DEPTH=3
uv run python -m harness.server settings --json cell-settings.json
uv run python -m harness.run --config experiments/exp-00N/config.yaml \
    --out experiments/exp-00N/data/raw/runs.jsonl --dry-run
uv run python -m harness.run --config experiments/exp-00N/config.yaml \
    --out experiments/exp-00N/data/raw/runs.jsonl
uv run python -m harness.server stop
```

`--dry-run` prints the plan, marking each run as `run` or `skip`. A run whose id
already appears in the output file is skipped, so an interrupted grid resumes
where it stopped.

## Base path and the admin API

The admin API requires an API key even over loopback unless the instance's own
settings file waives it. The harness does not change that setting on a server it
finds, and it never weakens a setting on an existing instance. A research
instance's base path therefore needs a `settings.json` containing:

```json
{"version": "1.0", "auth": {"skip_api_key_verification": true}}
```

`harness.server init-base` creates the base path, its `logs` and cache
directories and exactly that file, if the file is not already there. An existing
`settings.json` is left untouched; the command reports whether it waives key
verification and exits non-zero when it does not, so the alternative is to set
`OMLX_API_KEY` or edit that file yourself. The file it writes contains no host
name, address or credential.

This waiver is safe only because the research instance is bound to loopback. Do
not use it on anything reachable from a network.

## Server environment

`start --env KEY=VALUE` is repeatable and passes those variables into the server
process. They are recorded in `research-instance.json` and appear in every run
record's notes as `server_env`, so a run can prove which overrides the server was
started with. A value whose name suggests a credential is passed to the server in
full but written down as `<redacted>`, because both the instance file and the run
records are meant to be shareable.

The runner reads the overrides back from the instance file. A config may also
name a `server_env` mapping; if the running instance does not match it, the run
refuses to start rather than attributing its numbers to the wrong server.

## Config

```yaml
exp: exp-example
model: <model id>            # optional; falls back to MODEL
reload_on_change: true       # unload and load when a cell's settings change
defaults:
  endpoint: openai           # or anthropic
  max_tokens: 128
  seed: 0
cells:
  - name: mtp-off
    settings: {mtp_enabled: false}
    request: {max_tokens: 64, temperature: 0, seed: 7}
    workload: {prompt_tokens: 512, kind: code, seed: 1}
    repeats: 5
    warmup: 1            # one uncounted request before the repeats
    concurrency: 4       # four identical requests at once, one record each
    cache_clear: true    # clear the prefix caches before each request group
    record_output: true  # hash the completion and keep it in a sidecar file
  - name: session
    settings: {mtp_enabled: true, mtp_num_draft_tokens: 3}
    workload: {shape: workloads/shapes/read.yaml, seed: 2}
```

`request.temperature`, `request.seed`, `request.top_p` and `request.top_k` are
folded into the request body. A temperature of `0` is sent rather than dropped.

**Sending a sampling parameter is not the same as getting it.** This server has a
per-model `force_sampling` setting whose whole purpose is to override the
request's token-selection parameters with the model's own. With it on, a cell
asking for `temperature: 0` runs at whatever the model settings say, and nothing
in the response says so. EXP-002 lost its greedy correctness check that way and
found out afterwards, by reading the server's resolution code. If an experiment
depends on the sampler, read the instance's model settings for the model it will
serve before the first measured run, and record what they say next to what the
config asked for.

`warmup: N` issues N requests that are not counted and never written out, so the
runtime has settled before the first measured repeat.

`concurrency: N` issues N identical requests at the same time and writes one
record each, with the cell name unchanged. The notes carry `concurrency`, `slot`,
`repeat` and `group_wall_s`, the wall time of the whole group. Each slot has its
own run id, so an interrupted concurrent group resumes slot by slot.

`cache_clear: true` clears the hot and SSD prefix caches on the research instance
before each request. At a concurrency above 1 that means before each group, since
there is no moment between simultaneous requests. The record carries
`cache.cleared_before`.

`record_output: true` records `correctness.output_sha256` and
`correctness.output_token_count`, and appends the completion to a sidecar file
next to the run file, named `<out>.outputs.jsonl`, holding
`{run_id, cell, repeat, text}`. That text is the model's answer to a synthetic
prompt. The prompt itself is still never written anywhere.

Settings are applied once per cell, and only when the settings hash changes.
With `reload_on_change: true` the model is unloaded and loaded again on a change,
so a setting that is read at load time actually takes effect. The settings hash
is part of each run id, so changing a cell's settings produces new runs rather
than silently mixing two conditions under one name.

A workload is either a single prompt of a target length, or a shape from
`workloads/shapes/`, which produces one run per turn with the conversation
growing between turns.

## Timing

| Metric | Definition |
| --- | --- |
| `ttft_s` | request start to the first content delta |
| `decode_s` | first content delta to the last one |
| `e2e_s` | request start to the end of the stream |
| `prefill_s` | the server's own `prompt_eval_duration`; `null` when it reports none |
| `decode_tps` | `(completion_tokens - 1) / decode_s` |

The first emitted token is not a decode step, which is why `decode_tps` uses
`completion_tokens - 1`.

The server reports its own timings on the usage object, in seconds, and they are
kept in a `server_timing` block beside the client-measured ones rather than
instead of them: `ttft_s`, `visible_ttft_s`, `prefill_s`, `generation_s`,
`decode_tps`, `prompt_tps`, `total_s` and `model_load_s`. The server rounds them
to two decimals, which is 10 ms granularity, so the client's measurements stay
primary and the server's are there to cross-check them. `cached_tokens` is read
from `prompt_tokens_details.cached_tokens`, and the uncached suffix is the prompt
minus that.

The first request after a model load carries the load time inside the
client-measured `ttft_s`, because the client's clock starts before the server
begins loading. When the server reports a non-zero `model_load_duration` the
record's notes say `includes model load`, so that request can be excluded from a
latency comparison.

A streaming response carries no usage at all unless the request asks for it, so
the OpenAI path always sends `stream_options: {include_usage: true}`, set after
the caller's own body so it cannot be switched off by accident. On the Anthropic
path usage is split across the stream: `message_start` carries the input counts
and `message_delta` the output counts. Both are merged as the stream is read.

## Acceptance statistics

Two sources, and the harness prefers the first.

The server's usage object may carry `mtp_`-prefixed counters. Every such key is
mapped into the record's `mtp` block with the prefix stripped, including keys
this schema predates, so a counter added later needs no change here. A couple are
renamed to the record's own names: `mtp_accepted_tokens` becomes `accepted` and
`mtp_drafted_tokens` becomes `drafted`. `raw_usage` keeps every key verbatim
either way. `mtp.source` says where the numbers came from.

The fallback is the log. Draft acceptance is otherwise not in the inference API,
and the server writes one summary line
per finished sequence to `logs/server.log` under the base path.
`harness/mtp_log.py` marks the log position before a request and reads the lines
written during it, so a record is matched to a run by position and time window.

The parser anchors on named fields and skips anything it does not recognise
between them. That is not defensive style for its own sake. It originally
required `accept=` to follow `cycles=` immediately, and a build that prints a
derived `tok/cycle=` between the two matched nothing — so the fallback returned
no acceptance data at all, for every line, without reporting that it had failed
to read the format. A parser that cannot read a line should say so; one that
silently yields an empty result is indistinguishable from a server that never
speculated. The regression test for it is
`tests/test_harness.py::test_parses_a_line_carrying_a_derived_tok_per_cycle_field`.
The parser tolerates a line with no timing or depth section; the fields it cannot
find stay `null`. Usage counters win field by field where both are present,
because usage belongs to the request that reported it while a log line is matched
only by position and time window. At a concurrency above 1 the log lines cannot
be attributed to individual requests at all, so they are dropped and only the
usage counters are used; the record says so in its notes.

## Workloads

`workloads/generator.py` builds prompts from the word lists and templates in that
file. Nothing in it is copied from a real prompt, session or model output. A
prompt is grown until its tokenized length is within 3% of the target, measured
with the tokenizer from the model directory. Without `transformers` installed it
falls back to 4 characters per token, sets `approx: True`, and the runner records
that in the run's notes.

## Analysis

```python
from analysis.load import load_runs, summarize
frame = load_runs("experiments/exp-00N/data/raw/runs.jsonl")
summarize(frame, by=["cell"])
```

Summaries report n, median, min and max. There is no mean and no standard
deviation: with a handful of repeats on a shared machine, one scheduling
artifact moves the mean further than it moves the truth. `n` counts the runs
that actually measured a metric, so a partly unmeasured column says so.

`analysis/figures.py` carries the shared style, matching `figures/plot.py`, and
writes SVG.

## Tests

```sh
uv run pytest -q
```

No test opens a socket, starts a server or loads a model. The runner is driven
through an injected fake client.
