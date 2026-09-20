"""Tests for the research harness.

Nothing here starts a server, opens a socket or reads a model. The runner is
driven through an injected fake client.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

from analysis import load as analysis_load
from harness import mtp_log, run as run_mod, schema
from workloads import generator

REPO = pathlib.Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ schema


def test_blank_run_is_valid_once_identified():
    record = schema.blank_run()
    record.update({"run_id": "exp-001/a/r0", "exp": "exp-001", "cell": "a",
                   "model_id": "m", "timestamp": "2026-01-01T00:00:00Z"})
    schema.validate_run(record)


def test_missing_metric_is_null_not_zero():
    record = schema.blank_run()
    assert record["ttft_s"] is None
    assert record["mtp"]["accept_rate"] is None
    assert record["cached_tokens"] is None


def test_unknown_top_level_field_is_rejected():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t", "surprise": 1})
    assert not schema.is_valid_run(record)


def test_wrong_type_is_rejected():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t", "ttft_s": "fast"})
    assert not schema.is_valid_run(record)


def test_endpoint_is_constrained():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t", "endpoint": "grpc"})
    assert not schema.is_valid_run(record)


def test_settings_hash_is_order_independent():
    assert schema.settings_hash({"a": 1, "b": 2}) == schema.settings_hash({"b": 2, "a": 1})
    assert schema.settings_hash({"a": 1}) != schema.settings_hash({"a": 2})


def test_manifest_schema_loads_and_accepts_a_minimal_manifest():
    manifest = {
        "exp": "exp-999",
        "question": "Does drafting help at this batch size?",
        "hypothesis": "Acceptance falls below the break-even point.",
        "model": {"id": "a-model"},
        "server_sha": "abc123",
        "cells": [{"name": "baseline"}],
        "repeats": 3,
        "budget_hours": 2.0,
        "data": [{"path": "data/exp-999/runs.jsonl"}],
        "figures": [{"path": "figures/fig10.svg", "measured": True}],
        "evidence_level": "repeated",
    }
    schema.validate_manifest(manifest)


def test_manifest_rejects_an_unknown_evidence_level():
    import jsonschema

    manifest = {
        "exp": "exp-999", "question": "q", "hypothesis": "h",
        "model": {"id": "m"}, "server_sha": None, "cells": [{"name": "c"}],
        "repeats": 1, "budget_hours": 1, "data": [], "figures": [],
        "evidence_level": "vibes",
    }
    with pytest.raises(jsonschema.ValidationError):
        schema.validate_manifest(manifest)


# ---------------------------------------------------------------- mtp parse


FULL_LINE = (
    "2026-01-01 00:00:00 INFO MTP[seq-7f3] finish=stop tokens=64 cycles=22 "
    "accept=41/66 (62.1%) emits[init=1,draft=41,bonus=12,verify=22] "
    "timing[backbone=812.5ms mtp=104.2ms sample=31.0ms cache=9.5ms] "
    "depth[d1=18/22,d2=14/22,d3=9/22]"
)
MINIMAL_LINE = "MTP[u1] finish=length tokens=8 cycles=3 accept=5/9"


def test_parses_a_full_line():
    record = mtp_log.parse_line(FULL_LINE)
    assert record.uid == "seq-7f3"
    assert record.finish == "stop"
    assert (record.tokens, record.cycles) == (64, 22)
    assert (record.accepted, record.drafted) == (41, 66)
    assert record.accept_rate == pytest.approx(0.621)
    assert record.emits == {"init": 1, "draft": 41, "bonus": 12, "verify": 22}
    assert record.backbone_ms == pytest.approx(812.5)
    assert record.verify_ms == pytest.approx(104.2)
    assert record.depth_accepted == [18, 14, 9]
    assert record.depth_drafted == [22, 22, 22]


def test_parses_a_line_without_timing_or_depth():
    record = mtp_log.parse_line(MINIMAL_LINE)
    assert (record.accepted, record.drafted) == (5, 9)
    assert record.accept_rate == pytest.approx(5 / 9)
    assert record.backbone_ms is None
    assert record.verify_ms is None
    assert record.depth_accepted is None


# The served build prints a derived `tok/cycle=` between the cycle count and the
# acceptance pair; the build the parser was written against did not. Every line
# of a whole run was dropped silently before the parser learned to skip it.
TOK_PER_CYCLE_LINE = (
    "MTP[1] finish=stop tokens=335 cycles=127 tok/cycle=2.64 accept=207/257 "
    "(80.5%) depth[d1=111/124,d2=77/107,d3=19/26] d0=3 "
    "emits[init=2,draft=207,bonus=76,verify=50] "
    "timing[backbone=7407.0ms mtp=211.9ms sample=1.1ms cache=6.9ms]"
)


def test_parses_a_line_carrying_a_derived_tok_per_cycle_field():
    record = mtp_log.parse_line(TOK_PER_CYCLE_LINE)
    assert record is not None
    assert (record.tokens, record.cycles) == (335, 127)
    assert (record.accepted, record.drafted) == (207, 257)
    assert record.accept_rate == pytest.approx(0.805)
    assert record.backbone_ms == pytest.approx(7407.0)
    assert record.depth_accepted == [111, 77, 19]


def test_ignores_an_unrelated_line():
    assert mtp_log.parse_line("INFO model loaded in 12.4s") is None


def test_run_fields_carry_nulls_through():
    fields = mtp_log.parse_line(MINIMAL_LINE).as_run_fields(enabled=True, draft_tokens=3)
    assert fields["enabled"] is True
    assert fields["draft_tokens"] == 3
    assert fields["backbone_ms"] is None
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t"})
    record["mtp"] = fields
    schema.validate_run(record)


def test_tail_returns_only_lines_written_after_mark(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(MINIMAL_LINE + "\n")
    tail = mtp_log.MTPLogTail(log)
    tail.mark()
    assert tail.collect(timeout_s=0.05) == []
    with log.open("a") as fh:
        fh.write(FULL_LINE + "\n")
    found = tail.collect(timeout_s=1.0)
    assert len(found) == 1
    assert found[0].uid == "seq-7f3"


def test_tail_handles_a_truncated_log(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(FULL_LINE + "\n" + FULL_LINE + "\n")
    tail = mtp_log.MTPLogTail(log)
    tail.mark()
    log.write_text(MINIMAL_LINE + "\n")
    found = tail.collect(timeout_s=1.0)
    assert [r.uid for r in found] == ["u1"]


def test_tail_with_no_log_is_quiet():
    tail = mtp_log.MTPLogTail(None)
    tail.mark()
    assert tail.collect_one(timeout_s=0.05) is None


# ---------------------------------------------------------------- generator


def _has_tokenizer() -> bool:
    _, approx = generator.token_counter()
    return not approx


def test_generated_prompt_hits_the_token_target():
    count_fn, approx = generator.token_counter()
    if approx:
        pytest.skip("no tokenizer available; exact token targeting is untestable")
    for target in (256, 512, 2048):
        text, info = generator.make_prompt(target, kind="code", seed=1)
        assert abs(info["actual_tokens"] - target) <= target * 0.03
        assert info["approx"] is False
        assert text


def test_approximate_mode_hits_the_target_and_flags_itself():
    count_fn = lambda text: max(1, round(len(text) / 4.0))  # noqa: E731
    text, info = generator.make_prompt(800, kind="prose", seed=3,
                                       count_fn=count_fn, approx=True)
    assert info["approx"] is True
    assert abs(info["actual_tokens"] - 800) <= 800 * 0.03
    assert count_fn(text) == info["actual_tokens"]


def test_generation_is_deterministic_for_a_seed():
    count_fn = lambda text: max(1, round(len(text) / 4.0))  # noqa: E731
    first, _ = generator.make_prompt(400, kind="code", seed=7, count_fn=count_fn,
                                     approx=True)
    second, _ = generator.make_prompt(400, kind="code", seed=7, count_fn=count_fn,
                                      approx=True)
    third, _ = generator.make_prompt(400, kind="code", seed=8, count_fn=count_fn,
                                     approx=True)
    assert first == second
    assert first != third


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        generator.make_prompt(100, kind="sql", seed=0,
                              count_fn=lambda t: len(t) // 4, approx=True)


@pytest.mark.parametrize("name", ["read", "search", "crossfile", "edit",
                                  "testloop", "multiturn"])
def test_every_shape_builds_a_session(name):
    count_fn = lambda text: max(1, round(len(text) / 4.0))  # noqa: E731
    path = REPO / "workloads" / "shapes" / f"{name}.yaml"
    shape = yaml.safe_load(path.read_text())
    turns = generator.make_session(path, seed=2, count_fn=count_fn, approx=True)
    assert len(turns) == len(shape["turns"])
    for turn in turns:
        assert turn["text"]
        assert turn["output_max_tokens"] > 0
        assert abs(turn["actual_tokens"] - turn["add_tokens"]) <= turn["add_tokens"] * 0.03


# ------------------------------------------------------------------ runner


def _config(tmp_path: pathlib.Path, repeats: int = 2) -> dict:
    return {
        "exp": "exp-test",
        "model": "test-model",
        "reload_on_change": True,
        "defaults": {"endpoint": "openai", "max_tokens": 16, "seed": 1,
                     "prompt_tokens": 64, "kind": "prose"},
        "cells": [
            {"name": "mtp-off", "settings": {"mtp_enabled": False},
             "repeats": repeats,
             "workload": {"prompt_tokens": 64, "kind": "prose", "seed": 1}},
            {"name": "mtp-on",
             "settings": {"mtp_enabled": True, "mtp_num_draft_tokens": 3},
             "repeats": repeats,
             "workload": {"prompt_tokens": 64, "kind": "prose", "seed": 2}},
        ],
    }


class FakeClient:
    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return {
            "endpoint": kwargs.get("endpoint", "openai"),
            "prompt_tokens": 70,
            "cached_tokens": 0,
            "uncached_suffix_tokens": 70,
            "output_tokens": 16,
            "ttft_s": 0.25,
            "prefill_s": None,
            "decode_s": 0.5,
            "e2e_s": 0.8,
            "decode_tps": 30.0,
            "raw_usage": {"prompt_tokens": 70, "completion_tokens": 16},
        }


def _execute(config, out, client, applied, reloads, monkeypatch):
    monkeypatch.setattr(
        run_mod, "_turns_for_cell",
        lambda cell, defaults, model_dir=None, count_fn=None: [{
            "index": 0, "text": "generated", "add_tokens": 64,
            "actual_tokens": 64, "kind": "prose", "output_max_tokens": 16,
            "idle_s": 0.0, "approx": True,
        }],
    )
    return run_mod.execute(
        config, out,
        client_fn=client,
        apply_settings_fn=lambda values: applied.append(values) or values,
        reload_fn=lambda: reloads.append(1),
        server_sha="deadbeef",
        log_tail=mtp_log.MTPLogTail(None),
        sleep_fn=lambda seconds: None,
    )


def test_runner_writes_valid_records(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client, applied, reloads = FakeClient(), [], []
    written = _execute(_config(tmp_path), out, client, applied, reloads, monkeypatch)

    assert len(written) == 4
    assert client.calls == 4
    lines = out.read_text().splitlines()
    assert len(lines) == 4
    for line in lines:
        record = json.loads(line)
        schema.validate_run(record)
        assert record["server_git_sha"] == "deadbeef"
        assert record["ttft_s"] == 0.25
        assert "generated" not in line  # prompt text never reaches the file


def test_runner_applies_settings_once_per_cell_and_reloads_on_change(
        tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client, applied, reloads = FakeClient(), [], []
    _execute(_config(tmp_path), out, client, applied, reloads, monkeypatch)
    assert len(applied) == 2          # once per cell, not once per repeat
    assert len(reloads) == 1          # only on the change to the second cell


def test_runner_is_resumable(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    config = _config(tmp_path)

    first_client, applied, reloads = FakeClient(), [], []
    _execute(config, out, first_client, applied, reloads, monkeypatch)
    assert first_client.calls == 4

    second_client, applied2, reloads2 = FakeClient(), [], []
    written = _execute(config, out, second_client, applied2, reloads2, monkeypatch)
    assert second_client.calls == 0
    assert written == []
    assert applied2 == []             # a fully recorded cell is not touched
    assert len(out.read_text().splitlines()) == 4


def test_runner_resumes_a_partial_file(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    config = _config(tmp_path)
    _execute(config, out, FakeClient(), [], [], monkeypatch)

    kept = out.read_text().splitlines()[:3]
    out.write_text("\n".join(kept) + "\n")

    client = FakeClient()
    _execute(config, out, client, [], [], monkeypatch)
    assert client.calls == 1
    ids = [json.loads(line)["run_id"] for line in out.read_text().splitlines()]
    assert len(ids) == 4
    assert len(set(ids)) == 4


def test_plan_marks_recorded_runs_as_skipped(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    config = _config(tmp_path)
    entries = run_mod.plan(config, out)
    assert len(entries) == 4
    assert not any(entry["skip"] for entry in entries)

    _execute(config, out, FakeClient(), [], [], monkeypatch)
    assert all(entry["skip"] for entry in run_mod.plan(config, out))


def test_changing_settings_changes_the_run_id(tmp_path):
    out = tmp_path / "runs.jsonl"
    config = _config(tmp_path)
    before = {entry["run_id"] for entry in run_mod.plan(config, out)}
    config["cells"][0]["settings"]["mtp_enabled"] = True
    after = {entry["run_id"] for entry in run_mod.plan(config, out)}
    assert before != after


# ---------------------------------------------------------------- analysis


def test_load_and_summarize(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    _execute(_config(tmp_path), out, FakeClient(), [], [], monkeypatch)

    frame = analysis_load.load_runs(out)
    assert len(frame) == 4
    assert "mtp_accept_rate" in frame.columns
    assert "cache_hit" in frame.columns

    summary = analysis_load.summarize(frame, by=["cell"])
    assert len(summary) == 2
    assert set(summary["cell"]) == {"mtp-off", "mtp-on"}
    assert summary["ttft_s_median"].iloc[0] == pytest.approx(0.25)
    assert summary["ttft_s_n"].iloc[0] == 2
    assert summary["mtp_accept_rate_n"].iloc[0] == 0   # never measured, so n is 0

    long = analysis_load.tidy(frame, by=["cell"])
    assert {"metric", "median", "n"} <= set(long.columns)


def test_load_missing_file_is_empty(tmp_path):
    assert analysis_load.load_runs(tmp_path / "nope.jsonl").empty


# ------------------------------------------------------- preflight gates


class FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   264259.
Pages active:                                1834757.
Pages inactive:                              1620995.
Pages speculative:                            220947.
Pages wired down:                             174335.
Pages occupied by compressor:                  27722.
"""
PRESSURE = "Swapins: 1\nSystem-wide memory free percentage: 95%\n"


def _fake_run(monkeypatch, vm_stat=VM_STAT, pressure=PRESSURE):
    from harness import server as server_mod

    def fake(cmd, *args, **kwargs):
        if cmd[0] == "vm_stat":
            return FakeCompleted(vm_stat)
        if cmd[0] == "memory_pressure":
            return FakeCompleted(pressure)
        if cmd[0] == "ps":
            return FakeCompleted("  PID    RSS COMM\n")
        return FakeCompleted("")

    monkeypatch.setattr(server_mod.subprocess, "run", fake)
    monkeypatch.setattr(server_mod, "port_busy", lambda host, port: False)
    return server_mod


def _cfg(**kwargs):
    from harness import settings as settings_mod

    return settings_mod.load(host="127.0.0.1", port=1, model="m", **kwargs)


def test_wired_plus_compressor_excludes_active_pages(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    wired = server_mod.wired_plus_compressor_gb()
    assert wired == pytest.approx((174335 + 27722) * 16384 / 1024 ** 3)
    assert wired < 4  # the 28 GB of active pages is page cache, not held memory


def test_free_percentage_is_read_from_memory_pressure(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    assert server_mod.memory_free_percentage() == 95.0


def test_preflight_passes_on_a_healthy_machine(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    reasons, metrics = server_mod.preflight_report(_cfg())
    assert reasons == []
    assert metrics["free_pct"] == 95.0
    assert metrics["port_busy"] is False


def test_preflight_refuses_when_free_percentage_is_low(monkeypatch):
    server_mod = _fake_run(
        monkeypatch, pressure="System-wide memory free percentage: 41%\n")
    reasons, _ = server_mod.preflight_report(_cfg())
    assert any("41%" in reason and "60% floor" in reason for reason in reasons)


def test_preflight_refuses_when_wired_memory_is_high(monkeypatch):
    high = VM_STAT.replace("Pages wired down:                             174335.",
                           "Pages wired down:                            1800000.")
    server_mod = _fake_run(monkeypatch, vm_stat=high)
    reasons, _ = server_mod.preflight_report(_cfg())
    assert any("wired plus compressor" in reason for reason in reasons)


def test_preflight_refuses_a_busy_port(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    monkeypatch.setattr(server_mod, "port_busy", lambda host, port: True)
    reasons, metrics = server_mod.preflight_report(_cfg())
    assert metrics["port_busy"] is True
    assert any("already in use" in reason for reason in reasons)


def test_preflight_refuses_a_large_foreign_server(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    monkeypatch.setattr(
        server_mod, "foreign_processes",
        lambda name, pid: [(999, 31.0, "omlx-server")])
    reasons, _ = server_mod.preflight_report(_cfg())
    assert any("pid 999" in reason and "31.0 GB" in reason for reason in reasons)


def test_preflight_tolerates_a_small_foreign_server(monkeypatch):
    server_mod = _fake_run(monkeypatch)
    monkeypatch.setattr(
        server_mod, "foreign_processes",
        lambda name, pid: [(999, 3.5, "omlx-server")])
    reasons, metrics = server_mod.preflight_report(_cfg())
    assert reasons == []
    assert metrics["foreign_servers"] == [{"pid": 999, "rss_gb": 3.5}]


# ------------------------------------------------------- mtp usage fields


def test_mtp_usage_keys_are_stripped_and_renamed():
    from harness import client as client_mod

    fields = client_mod.mtp_fields_from_usage({
        "prompt_tokens": 10,
        "mtp_cycles": 22,
        "mtp_accepted_tokens": 41,
        "mtp_drafted_tokens": 66,
        "mtp_accept_rate": 0.62,
        "mtp_zero_cycles": 2,
        "mtp_backbone_ms": 812.5,
        "mtp_head_ms": 104.2,
        "mtp_sample_ms": 31.0,
        "mtp_cache_ops_ms": 9.5,
        "mtp_depth_accepted": [18, 14, 9],
        "mtp_depth_drafted": [22, 22, 22],
        "mtp_backbone_ms_is_equal_share": True,
        "mtp_a_counter_added_later": 7,
    })
    assert fields["cycles"] == 22
    assert fields["accepted"] == 41
    assert fields["drafted"] == 66
    assert fields["depth_accepted"] == [18, 14, 9]
    assert fields["backbone_ms_is_equal_share"] is True
    assert fields["a_counter_added_later"] == 7
    assert "prompt_tokens" not in fields


def test_no_mtp_usage_keys_gives_an_empty_mapping():
    from harness import client as client_mod

    assert client_mod.mtp_fields_from_usage({"prompt_tokens": 10}) == {}
    assert client_mod.mtp_fields_from_usage(None) == {}


def test_unknown_mtp_key_survives_schema_validation():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t"})
    record["mtp"]["a_counter_added_later"] = 7
    record["mtp"]["source"] = "usage"
    schema.validate_run(record)


def test_usage_counters_win_over_the_log_line():
    record = schema.blank_run()
    observed = mtp_log.parse_line(FULL_LINE)
    run_mod._fill_mtp(record, {"mtp_enabled": True, "mtp_num_draft_tokens": 3},
                      observed, {"cycles": 99, "accepted": 77}, ambiguous=False)
    assert record["mtp"]["cycles"] == 99        # from usage
    assert record["mtp"]["accepted"] == 77      # from usage
    assert record["mtp"]["drafted"] == 66       # only the log had it
    assert record["mtp"]["source"] == "usage+log"


def test_log_line_is_ignored_when_concurrency_makes_it_ambiguous():
    record = schema.blank_run()
    observed = mtp_log.parse_line(FULL_LINE)
    run_mod._fill_mtp(record, {}, observed, {"cycles": 5}, ambiguous=True)
    assert record["mtp"]["cycles"] == 5
    assert record["mtp"]["drafted"] is None
    assert record["mtp"]["source"] == "usage"


def test_source_is_null_when_nothing_reported():
    record = schema.blank_run()
    run_mod._fill_mtp(record, {}, None, {}, ambiguous=False)
    assert record["mtp"]["source"] is None


# --------------------------------------------------- shadow usage fields


def test_blank_run_shadow_fields_are_null():
    record = schema.blank_run()
    for key in ("arm", "budget_pct", "longest_canonical_prefix_tokens",
                "canonical_debt_tokens", "committed_tokens", "target_tokens",
                "runnable_steps", "scheduled_steps", "yielded_steps",
                "resumed_steps", "service_s", "service_share", "publishes",
                "restores", "scorer_s", "dense_tail_s"):
        assert record["shadow"][key] is None


def test_no_shadow_usage_keys_gives_an_empty_mapping():
    from harness import client as client_mod

    assert client_mod.shadow_fields_from_usage({"prompt_tokens": 10}) == {}
    assert client_mod.shadow_fields_from_usage(None) == {}
    assert client_mod.shadow_fields_from_usage({}) == {}


def test_shadow_usage_keys_are_stripped():
    from harness import client as client_mod

    fields = client_mod.shadow_fields_from_usage({
        "prompt_tokens": 10,
        "shadow_committed_tokens": 128,
    })
    assert fields == {"committed_tokens": 128}


def test_shadow_populated_record_validates():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t"})
    record["shadow"].update({
        "arm": "shadow-end",
        "budget_pct": 0.4,
        "longest_canonical_prefix_tokens": 512,
        "canonical_debt_tokens": 16,
        "committed_tokens": 128,
        "target_tokens": 160,
        "runnable_steps": 4,
        "scheduled_steps": 3,
        "yielded_steps": 1,
        "resumed_steps": 1,
        "service_s": 0.31,
        "service_share": 0.5,
        "publishes": 2,
        "restores": 1,
        "scorer_s": 0.02,
        "dense_tail_s": 0.1,
    })
    schema.validate_run(record)


# ------------------------------------- concurrency, warm-up, cache, output


class RecordingClient(FakeClient):
    """A fake client that records its kwargs and how many overlap in time."""

    def __init__(self, text: str | None = None, usage_extra: dict | None = None,
                 hold_s: float = 0.0):
        super().__init__()
        self.kwargs = []
        self.text = text
        self.usage_extra = usage_extra or {}
        self.hold_s = hold_s
        self.peak_in_flight = 0
        self._in_flight = 0
        self._lock = __import__("threading").Lock()

    def __call__(self, **kwargs):
        with self._lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            if self.hold_s:
                time.sleep(self.hold_s)
            result = super().__call__(**kwargs)
        finally:
            with self._lock:
                self._in_flight -= 1
        with self._lock:
            self.kwargs.append(kwargs)
        if self.text is not None and kwargs.get("capture_text"):
            result["output_text"] = self.text
        result["raw_usage"] = {**result["raw_usage"], **self.usage_extra}
        from harness import client as client_mod

        result["mtp_from_usage"] = client_mod.mtp_fields_from_usage(result["raw_usage"])
        return result


import time  # noqa: E402  (used by RecordingClient above)


def _run(config, out, client, monkeypatch, clears=None):
    monkeypatch.setattr(
        run_mod, "_turns_for_cell",
        lambda cell, defaults, model_dir=None, count_fn=None: [{
            "index": 0, "text": "generated", "add_tokens": 64,
            "actual_tokens": 64, "kind": "prose", "output_max_tokens": 16,
            "idle_s": 0.0, "approx": False,
        }],
    )
    return run_mod.execute(
        config, out,
        client_fn=client,
        apply_settings_fn=lambda values: values,
        reload_fn=lambda: None,
        clear_caches_fn=(lambda: clears.append(1)) if clears is not None else (lambda: None),
        server_sha="sha",
        log_tail=mtp_log.MTPLogTail(None),
        sleep_fn=lambda seconds: None,
    )


def _one_cell(**cell) -> dict:
    base = {"name": "c1", "settings": {"mtp_enabled": True}, "repeats": 1,
            "workload": {"prompt_tokens": 64, "kind": "prose", "seed": 1}}
    base.update(cell)
    return {"exp": "exp-test", "model": "m", "defaults": {"max_tokens": 16},
            "cells": [base]}


def test_concurrency_issues_requests_simultaneously(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient(hold_s=0.05)
    written = _run(_one_cell(concurrency=4, repeats=2), out, client, monkeypatch)

    assert len(written) == 8            # 4 slots x 2 repeats
    assert client.calls == 8
    assert client.peak_in_flight == 4   # the group really overlapped
    assert len({record["run_id"] for record in written}) == 8
    assert {record["cell"] for record in written} == {"c1"}
    for record in written:
        schema.validate_run(record)
        assert "concurrency=4" in record["notes"]
        assert "group_wall_s=" in record["notes"]
        assert f"repeat={record['repeat']}" in record["notes"]
    assert {note_value(r["notes"], "slot") for r in written} == {"0", "1", "2", "3"}


def note_value(notes: str, key: str) -> str | None:
    for part in notes.split("; "):
        if part.startswith(f"{key}="):
            return part.split("=", 1)[1]
    return None


def test_concurrency_defaults_to_one(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient()
    written = _run(_one_cell(), out, client, monkeypatch)
    assert len(written) == 1
    assert client.peak_in_flight == 1
    assert "concurrency=" not in written[0]["notes"]
    assert "repeat=0" in written[0]["notes"]


def test_concurrent_runs_are_resumable_slot_by_slot(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    config = _one_cell(concurrency=3)
    _run(config, out, RecordingClient(), monkeypatch)
    kept = out.read_text().splitlines()[:1]
    out.write_text(kept[0] + "\n")

    client = RecordingClient()
    _run(config, out, client, monkeypatch)
    assert client.calls == 2
    assert len(out.read_text().splitlines()) == 3


def test_warmup_request_is_not_counted(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient()
    written = _run(_one_cell(warmup=1, repeats=2), out, client, monkeypatch)
    assert client.calls == 3            # one warm-up plus two counted
    assert len(written) == 2
    assert len(out.read_text().splitlines()) == 2
    assert client.kwargs[0]["capture_text"] is False


def test_no_warmup_by_default(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient()
    _run(_one_cell(repeats=2), out, client, monkeypatch)
    assert client.calls == 2


def test_cache_clear_runs_before_each_request_group(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    clears = []
    written = _run(_one_cell(cache_clear=True, repeats=3), out,
                   RecordingClient(), monkeypatch, clears=clears)
    assert len(clears) == 3
    assert all(record["cache"]["cleared_before"] is True for record in written)


def test_cache_is_not_cleared_unless_asked(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    clears = []
    written = _run(_one_cell(repeats=2), out, RecordingClient(), monkeypatch,
                   clears=clears)
    assert clears == []
    assert all(record["cache"]["cleared_before"] is None for record in written)


def test_record_output_writes_a_sidecar_and_a_hash(tmp_path, monkeypatch):
    import hashlib

    out = tmp_path / "runs.jsonl"
    client = RecordingClient(text="the answer")
    written = _run(_one_cell(record_output=True, repeats=2), out, client, monkeypatch)

    expected = hashlib.sha256(b"the answer").hexdigest()
    for record in written:
        assert record["correctness"]["output_sha256"] == expected
        assert record["correctness"]["output_token_count"] == 16

    sidecar = tmp_path / "runs.jsonl.outputs.jsonl"
    rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert len(rows) == 2
    assert {row["text"] for row in rows} == {"the answer"}
    assert {row["cell"] for row in rows} == {"c1"}
    # the completion is in the sidecar and the prompt is in neither file
    assert "the answer" not in out.read_text()
    assert "generated" not in out.read_text()
    assert "generated" not in sidecar.read_text()


def test_no_sidecar_without_record_output(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    written = _run(_one_cell(), out, RecordingClient(text="x"), monkeypatch)
    assert not (tmp_path / "runs.jsonl.outputs.jsonl").exists()
    assert written[0]["correctness"]["output_sha256"] is None


def test_request_temperature_and_seed_reach_the_request(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient()
    config = _one_cell(request={"temperature": 0, "seed": 4321, "max_tokens": 16})
    _run(config, out, client, monkeypatch)
    body = client.kwargs[0]["extra_body"]
    assert body["temperature"] == 0     # zero is sent, not dropped as falsy
    assert body["seed"] == 4321


def test_usage_mtp_counters_land_in_the_record(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    client = RecordingClient(usage_extra={"mtp_cycles": 12, "mtp_accepted_tokens": 30,
                                          "mtp_accept_rate": 0.83})
    written = _run(_one_cell(), out, client, monkeypatch)
    record = written[0]
    assert record["mtp"]["cycles"] == 12
    assert record["mtp"]["accepted"] == 30
    assert record["mtp"]["accept_rate"] == 0.83
    assert record["mtp"]["source"] == "usage"
    assert record["raw_usage"]["mtp_accepted_tokens"] == 30   # kept verbatim
    schema.validate_run(record)


# -------------------------------------------------- streaming usage opt-in


def test_streaming_openai_request_asks_for_usage():
    from harness import client as client_mod

    path, body = client_mod.build_request(
        [{"role": "user", "content": "x"}], 8, "m", "openai", True, None)
    assert path == "/v1/chat/completions"
    assert body["stream_options"] == {"include_usage": True}


def test_include_usage_cannot_be_switched_off_by_extra_body():
    from harness import client as client_mod

    _, body = client_mod.build_request(
        [{"role": "user", "content": "x"}], 8, "m", "openai", True,
        {"stream_options": {"include_usage": False, "other": 1}})
    assert body["stream_options"]["include_usage"] is True
    assert body["stream_options"]["other"] == 1


def test_non_streaming_request_has_no_stream_options():
    from harness import client as client_mod

    _, body = client_mod.build_request(
        [{"role": "user", "content": "x"}], 8, "m", "openai", False, None)
    assert "stream_options" not in body


def test_anthropic_streaming_usage_is_merged_across_events():
    from harness import client as client_mod

    merged: dict = {}
    events = [
        {"type": "message_start",
         "message": {"usage": {"input_tokens": 512, "cache_read_input_tokens": 100}}},
        {"type": "content_block_delta", "delta": {"text": "hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 64, "mtp_cycles": 20,
                   "mtp_backbone_ms_is_equal_share": False}},
    ]
    for event in events:
        client_mod._merge_usage(merged, client_mod._dig_usage(event))

    assert merged["input_tokens"] == 512
    assert merged["output_tokens"] == 64
    assert merged["cache_read_input_tokens"] == 100
    assert client_mod.mtp_fields_from_usage(merged)["cycles"] == 20
    assert client_mod.mtp_fields_from_usage(merged)["backbone_ms_is_equal_share"] is False


def test_anthropic_usage_becomes_run_fields():
    from harness import client as client_mod

    fields = client_mod._assemble(
        {"input_tokens": 512, "output_tokens": 64, "cache_read_input_tokens": 100},
        "anthropic", ttft=0.2, decode=0.6, e2e=0.9,
        text_chars=10, delta_count=5, streamed=True)
    assert fields["prompt_tokens"] == 512
    assert fields["output_tokens"] == 64
    assert fields["cached_tokens"] == 100
    assert fields["uncached_suffix_tokens"] == 412
    assert fields["decode_tps"] == pytest.approx(63 / 0.6)


# --------------------------------------------------- server env overrides


def test_env_pairs_are_parsed():
    from harness import server as server_mod

    assert server_mod.parse_env_overrides(["A=1", "B=x=y"]) == {"A": "1", "B": "x=y"}
    assert server_mod.parse_env_overrides(None) == {}


def test_malformed_env_pair_is_refused():
    from harness import server as server_mod

    with pytest.raises(SystemExit):
        server_mod.parse_env_overrides(["NOEQUALS"])
    with pytest.raises(SystemExit):
        server_mod.parse_env_overrides(["=value"])


def test_credential_looking_values_are_redacted():
    from harness import server as server_mod

    safe = server_mod.redact_env({"OMLX_MTP_FIXED_DEPTH": "3",
                                  "OMLX_API_KEY": "hunter2",
                                  "SOME_TOKEN": "abc"})
    assert safe["OMLX_MTP_FIXED_DEPTH"] == "3"
    assert safe["OMLX_API_KEY"] == server_mod.REDACTED
    assert safe["SOME_TOKEN"] == server_mod.REDACTED


def test_env_note_is_sorted_and_redacted():
    from harness import server as server_mod

    note = server_mod.format_env({"B": "2", "A": "1", "A_SECRET": "s"})
    assert note == f"A=1,A_SECRET={server_mod.REDACTED},B=2"
    assert server_mod.format_env({}) is None
    assert server_mod.format_env(None) is None


def test_server_env_reaches_every_run_record(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    monkeypatch.setattr(
        run_mod, "_turns_for_cell",
        lambda cell, defaults, model_dir=None, count_fn=None: [{
            "index": 0, "text": "generated", "add_tokens": 64, "actual_tokens": 64,
            "kind": "prose", "output_max_tokens": 16, "idle_s": 0.0, "approx": False,
        }],
    )
    written = run_mod.execute(
        _one_cell(repeats=2), out,
        client_fn=RecordingClient(),
        apply_settings_fn=lambda values: values,
        reload_fn=lambda: None,
        clear_caches_fn=lambda: None,
        server_sha="sha",
        server_env={"OMLX_MTP_FIXED_DEPTH": "3"},
        log_tail=mtp_log.MTPLogTail(None),
        sleep_fn=lambda seconds: None,
    )
    assert len(written) == 2
    for record in written:
        assert "server_env=OMLX_MTP_FIXED_DEPTH=3" in record["notes"]


def test_start_passes_env_and_records_it(tmp_path, monkeypatch):
    from harness import server as server_mod
    from harness import settings as settings_mod

    base = tmp_path / "base"
    cfg = settings_mod.load(bin=tmp_path / "omlx", src=None,
                            model_dir=tmp_path, base_path=base,
                            ssd_cache_dir=base / "cache", model="m", port=1)
    (tmp_path / "omlx").write_text("#!/bin/sh\n")

    seen = {}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        seen["env"] = kwargs["env"]
        seen["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(server_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_mod, "preflight", lambda cfg, own_pid=None: [])
    monkeypatch.setattr(server_mod, "wait_for_health",
                        lambda cfg, timeout_s=0, poll_s=0, proc=None: True)
    monkeypatch.setattr(server_mod, "server_git_sha", lambda cfg: "sha")

    payload = server_mod.start(cfg, extra_env={"OMLX_MTP_FIXED_DEPTH": "3",
                                               "OMLX_API_KEY": "hunter2"})

    assert seen["env"]["OMLX_MTP_FIXED_DEPTH"] == "3"
    assert seen["env"]["OMLX_API_KEY"] == "hunter2"   # the real value is passed
    assert payload["pid"] == 4242
    recorded = json.loads((base / "research-instance.json").read_text())
    assert recorded["server_env"]["OMLX_MTP_FIXED_DEPTH"] == "3"
    assert recorded["server_env"]["OMLX_API_KEY"] == server_mod.REDACTED
    assert "hunter2" not in (base / "research-instance.json").read_text()


# ------------------------------------------------- server-reported timing


SERVER_USAGE = {
    "prompt_tokens": 512,
    "completion_tokens": 64,
    "prompt_tokens_details": {"cached_tokens": 100},
    "time_to_first_token": 0.41,
    "time_to_first_visible_token": 0.44,
    "prompt_eval_duration": 0.35,
    "generation_duration": 1.2,
    "generation_tokens_per_second": 52.3,
    "prompt_tokens_per_second": 1460.0,
    "total_time": 1.6,
    "model_load_duration": 0.0,
}


def _assembled(usage=None, **over):
    from harness import client as client_mod

    merged = {**SERVER_USAGE, **(usage or {})}
    kwargs = {"ttft": 0.44, "decode": 1.2, "e2e": 1.7, "text_chars": 10,
              "delta_count": 5, "streamed": True}
    kwargs.update(over)
    return client_mod._assemble(merged, "openai", **kwargs)


def test_prefill_comes_from_prompt_eval_duration():
    assert _assembled()["prefill_s"] == 0.35


def test_prefill_is_null_when_the_server_reports_none():
    fields = _assembled({"prompt_eval_duration": None})
    assert fields["prefill_s"] is None       # never silently equal to ttft_s
    assert fields["ttft_s"] == 0.44


def test_cached_tokens_come_from_prompt_tokens_details():
    fields = _assembled()
    assert fields["cached_tokens"] == 100
    assert fields["uncached_suffix_tokens"] == 412


def test_uncached_is_the_whole_prompt_when_nothing_was_cached():
    fields = _assembled({"prompt_tokens_details": {"cached_tokens": 0}})
    assert fields["cached_tokens"] == 0
    assert fields["uncached_suffix_tokens"] == 512


def test_server_timings_are_recorded_beside_the_client_ones():
    fields = _assembled()
    timing = fields["server_timing"]
    assert timing["ttft_s"] == 0.41          # the server's own figure
    assert fields["ttft_s"] == 0.44          # the client's stays primary
    assert timing["decode_tps"] == 52.3
    assert fields["decode_tps"] == pytest.approx(63 / 1.2)
    assert timing["prompt_tps"] == 1460.0
    assert timing["total_s"] == 1.6
    assert timing["generation_s"] == 1.2
    assert timing["visible_ttft_s"] == 0.44
    assert timing["model_load_s"] == 0.0


def test_absent_server_timings_are_null_not_zero():
    from harness import client as client_mod

    timing = client_mod.server_timing_from_usage({"prompt_tokens": 1})
    assert set(timing) == {"ttft_s", "visible_ttft_s", "prefill_s", "generation_s",
                           "decode_tps", "prompt_tps", "total_s", "model_load_s"}
    assert all(value is None for value in timing.values())
    assert client_mod.server_timing_from_usage(None)["ttft_s"] is None


def test_model_load_is_flagged_only_when_it_happened():
    assert _assembled()["includes_model_load"] is False
    assert _assembled({"model_load_duration": 12.5})["includes_model_load"] is True


def test_server_timing_validates_and_accepts_a_new_key():
    record = schema.blank_run()
    record.update({"run_id": "r", "exp": "e", "cell": "c", "model_id": "m",
                   "timestamp": "t"})
    record["server_timing"] = _assembled()["server_timing"]
    schema.validate_run(record)
    record["server_timing"]["a_timing_added_later"] = 1.0
    schema.validate_run(record)


class TimingClient(FakeClient):
    def __init__(self, model_load_s=0.0):
        super().__init__()
        self.model_load_s = model_load_s

    def __call__(self, **kwargs):
        result = super().__call__(**kwargs)
        timing = dict(_assembled({"model_load_duration": self.model_load_s})
                      ["server_timing"])
        result["server_timing"] = timing
        result["prefill_s"] = timing["prefill_s"]
        result["includes_model_load"] = bool(self.model_load_s)
        result["mtp_from_usage"] = {}
        return result


def test_runner_records_server_timing(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    written = _run(_one_cell(), out, TimingClient(), monkeypatch)
    record = written[0]
    assert record["server_timing"]["ttft_s"] == 0.41
    assert record["prefill_s"] == 0.35
    assert "includes model load" not in record["notes"]
    schema.validate_run(record)


def test_runner_flags_the_request_that_loaded_the_model(tmp_path, monkeypatch):
    out = tmp_path / "runs.jsonl"
    written = _run(_one_cell(), out, TimingClient(model_load_s=12.5), monkeypatch)
    assert "includes model load" in written[0]["notes"]


# ------------------------------------------------- settings readback route


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_settings_are_read_from_the_models_listing(monkeypatch):
    from harness import server as server_mod

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return FakeResponse({"models": [
            {"id": "other", "settings": {"mtp_enabled": False}},
            {"id": "m", "settings": {"mtp_enabled": True, "mtp_num_draft_tokens": 3}},
        ]})

    monkeypatch.setattr(server_mod.httpx, "get", fake_get)
    values = server_mod.get_settings(_cfg())
    assert seen["url"].endswith("/admin/api/models")   # not the per-model route
    assert values == {"mtp_enabled": True, "mtp_num_draft_tokens": 3}


def test_settings_readback_accepts_a_bare_list(monkeypatch):
    from harness import server as server_mod

    monkeypatch.setattr(server_mod.httpx, "get",
                        lambda url, **kw: FakeResponse([{"id": "m",
                                                         "settings": {"a": 1}}]))
    assert server_mod.get_settings(_cfg()) == {"a": 1}


def test_settings_readback_refuses_an_absent_model(monkeypatch):
    from harness import server as server_mod

    monkeypatch.setattr(server_mod.httpx, "get",
                        lambda url, **kw: FakeResponse({"models": []}))
    with pytest.raises(SystemExit):
        server_mod.get_settings(_cfg())


def test_apply_settings_puts_to_the_per_model_route(monkeypatch):
    from harness import server as server_mod

    seen = {}

    def fake_put(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs["json"]
        return FakeResponse({})

    monkeypatch.setattr(server_mod.httpx, "put", fake_put)
    monkeypatch.setattr(server_mod, "get_settings",
                        lambda cfg, model=None: {"mtp_enabled": True})
    result = server_mod.apply_settings(_cfg(), {"mtp_enabled": True})
    assert seen["url"].endswith("/admin/api/models/m/settings")
    assert seen["json"] == {"mtp_enabled": True}
    assert result == {"mtp_enabled": True}


# -------------------------------------------------------------- init-base


def test_init_base_creates_the_settings_file(tmp_path):
    from harness import server as server_mod

    base = tmp_path / "base"
    cfg = _cfg(base_path=base, ssd_cache_dir=base / "cache")
    result = server_mod.init_base(cfg)

    assert result["created"] is True
    assert result["skip_api_key_verification"] is True
    assert (base / "logs").is_dir()
    assert (base / "cache").is_dir()
    written = json.loads((base / "settings.json").read_text())
    assert written["auth"]["skip_api_key_verification"] is True
    # nothing about this machine is written into the file
    assert "server" not in written


def test_init_base_leaves_an_existing_file_alone(tmp_path):
    from harness import server as server_mod

    base = tmp_path / "base"
    base.mkdir()
    (base / "settings.json").write_text(json.dumps({"mine": True}))
    cfg = _cfg(base_path=base, ssd_cache_dir=base / "cache")
    result = server_mod.init_base(cfg)

    assert result["created"] is False
    assert result["skip_api_key_verification"] is False   # reported, not fixed
    assert json.loads((base / "settings.json").read_text()) == {"mine": True}


def test_init_base_reports_an_existing_waiver(tmp_path):
    from harness import server as server_mod

    base = tmp_path / "base"
    base.mkdir()
    (base / "settings.json").write_text(
        json.dumps({"auth": {"skip_api_key_verification": True}}))
    cfg = _cfg(base_path=base, ssd_cache_dir=base / "cache")
    assert server_mod.init_base(cfg)["skip_api_key_verification"] is True


def test_init_base_needs_a_base_path(monkeypatch):
    from harness import server as server_mod

    monkeypatch.delenv("OMLX_RESEARCH_BASE", raising=False)
    with pytest.raises(SystemExit):
        server_mod.init_base(_cfg())
