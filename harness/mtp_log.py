"""Parse the server's per-sequence multi-token-prediction log lines.

Native acceptance statistics are not exposed by the inference API. The server
writes one line per finished sequence to logs/server.log:

    MTP[<uid>] finish=<reason> tokens=<N> cycles=<C> accept=<A>/<C> (<rate>%)
      emits[init=<i>,draft=<d>,bonus=<b>,verify=<v>]
      timing[backbone=<X>ms mtp=<Y>ms sample=<S>ms cache=<C>ms]
      depth[d1=<a>/<d>,d2=<a>/<d>,...]

The emits, timing and depth sections may be missing. This parser is tolerant of
that and of extra whitespace. It reads token counts and timings only; no prompt
or completion text appears on these lines.
"""

from __future__ import annotations

import pathlib
import re
import time
from dataclasses import dataclass, field

LINE_RE = re.compile(
    r"MTP\[(?P<uid>[^\]]*)\]\s+"
    r"finish=(?P<finish>\S+)\s+"
    r"tokens=(?P<tokens>\d+)\s+"
    r"cycles=(?P<cycles>\d+)\s+"
    r"accept=(?P<accepted>\d+)/(?P<drafted>\d+)"
    r"(?:\s+\((?P<rate>[\d.]+)%\))?"
)
EMITS_RE = re.compile(r"emits\[(?P<body>[^\]]*)\]")
TIMING_RE = re.compile(r"timing\[(?P<body>[^\]]*)\]")
DEPTH_RE = re.compile(r"depth\[(?P<body>[^\]]*)\]")
KV_RE = re.compile(r"(\w+)=([\d.]+)")
DEPTH_ENTRY_RE = re.compile(r"d(\d+)=(\d+)/(\d+)")


@dataclass
class MTPRecord:
    """One parsed MTP line. Unknown fields stay None."""

    uid: str | None = None
    finish: str | None = None
    tokens: int | None = None
    cycles: int | None = None
    accepted: int | None = None
    drafted: int | None = None
    accept_rate: float | None = None
    emits: dict[str, float] = field(default_factory=dict)
    timing_ms: dict[str, float] = field(default_factory=dict)
    depth_accepted: list[int] | None = None
    depth_drafted: list[int] | None = None
    observed_at: float | None = None
    raw: str | None = None

    @property
    def backbone_ms(self) -> float | None:
        return self.timing_ms.get("backbone")

    @property
    def verify_ms(self) -> float | None:
        return self.timing_ms.get("mtp")

    def as_run_fields(self, enabled: bool | None = None,
                      draft_tokens: int | None = None) -> dict:
        """A complete `mtp` sub-object of a run record.

        Every key the schema names is present, so this can replace the block
        outright. What the log line did not carry stays null.
        """
        from . import schema

        block = schema.blank_run()["mtp"]
        block.update({
            "enabled": enabled,
            "draft_tokens": draft_tokens,
            "cycles": self.cycles,
            "accepted": self.accepted,
            "drafted": self.drafted,
            "accept_rate": self.accept_rate,
            "verify_ms": self.verify_ms,
            "backbone_ms": self.backbone_ms,
            "depth_accepted": self.depth_accepted,
            "depth_drafted": self.depth_drafted,
            "sample_ms": self.timing_ms.get("sample"),
            "cache_ops_ms": self.timing_ms.get("cache"),
            "source": "log",
        })
        return block


def parse_line(line: str, observed_at: float | None = None) -> MTPRecord | None:
    """Parse one log line, or return None if it is not an MTP summary line."""
    match = LINE_RE.search(line)
    if match is None:
        return None
    accepted = int(match["accepted"])
    drafted = int(match["drafted"])
    rate = float(match["rate"]) / 100.0 if match["rate"] is not None else (
        accepted / drafted if drafted else None
    )
    record = MTPRecord(
        uid=match["uid"] or None,
        finish=match["finish"],
        tokens=int(match["tokens"]),
        cycles=int(match["cycles"]),
        accepted=accepted,
        drafted=drafted,
        accept_rate=rate,
        observed_at=observed_at if observed_at is not None else time.time(),
        raw=line.rstrip("\n"),
    )
    emits = EMITS_RE.search(line)
    if emits:
        record.emits = {k: float(v) for k, v in KV_RE.findall(emits["body"])}
    timing = TIMING_RE.search(line)
    if timing:
        record.timing_ms = {k: float(v) for k, v in KV_RE.findall(timing["body"])}
    depth = DEPTH_RE.search(line)
    if depth:
        entries = sorted(
            ((int(d), int(a), int(t)) for d, a, t in DEPTH_ENTRY_RE.findall(depth["body"]))
        )
        if entries:
            record.depth_accepted = [a for _, a, _ in entries]
            record.depth_drafted = [t for _, _, t in entries]
    return record


def parse_text(text: str) -> list[MTPRecord]:
    """Parse every MTP line in a block of log text, in order."""
    out = []
    for line in text.splitlines():
        record = parse_line(line)
        if record is not None:
            out.append(record)
    return out


class MTPLogTail:
    """Follow a server log and hand back the MTP lines a run produced.

    Usage: call `mark()` before a request and `collect()` after it. Only lines
    written between the two calls are returned, so a record is associated with
    a run by position in the log and by time window.
    """

    def __init__(self, path: pathlib.Path | str | None):
        self.path = pathlib.Path(path) if path else None
        self._offset = 0
        self.available = bool(self.path and self.path.exists())
        if self.available:
            self._offset = self.path.stat().st_size

    def mark(self) -> None:
        """Skip past everything written so far."""
        if self.path and self.path.exists():
            self.available = True
            self._offset = self.path.stat().st_size

    def _read_new(self) -> str:
        if not (self.path and self.path.exists()):
            return ""
        size = self.path.stat().st_size
        if size < self._offset:  # the log was rotated or truncated
            self._offset = 0
        with self.path.open("r", errors="replace") as fh:
            fh.seek(self._offset)
            text = fh.read()
            self._offset = fh.tell()
        return text

    def collect(self, timeout_s: float = 2.0, poll_s: float = 0.1) -> list[MTPRecord]:
        """Read lines written since `mark()`, waiting briefly for a late flush."""
        deadline = time.monotonic() + timeout_s
        records: list[MTPRecord] = []
        buffer = ""
        while True:
            buffer += self._read_new()
            records = parse_text(buffer)
            if records or time.monotonic() >= deadline:
                break
            time.sleep(poll_s)
        return records

    def collect_one(self, timeout_s: float = 2.0) -> MTPRecord | None:
        """The last MTP record of the window, or None if the log had none."""
        records = self.collect(timeout_s=timeout_s)
        return records[-1] if records else None
