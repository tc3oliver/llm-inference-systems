"""Load run records into a DataFrame and summarize them.

Summaries report n, median, min and max. No mean and no standard deviation:
with a handful of repeats on a shared machine the distribution is not normal
and an outlier moves the mean more than it moves the conclusion.
"""

from __future__ import annotations

import json
import pathlib

import pandas as pd

NESTED = ("spec", "mtp", "cache", "correctness", "shadow")
DEFAULT_METRICS = (
    "ttft_s",
    "prefill_s",
    "decode_s",
    "e2e_s",
    "decode_tps",
    "prompt_tokens",
    "cached_tokens",
    "uncached_suffix_tokens",
    "output_tokens",
    "mtp_accept_rate",
    "mtp_accepted",
    "mtp_drafted",
    "mtp_backbone_ms",
    "mtp_verify_ms",
)


def load_runs(path: str | pathlib.Path) -> pd.DataFrame:
    """Read a JSONL run file into a flat DataFrame.

    Nested objects are flattened to `mtp_accept_rate`, `cache_hit` and so on.
    List-valued fields and raw_usage are kept as objects in their own columns.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        flat = {k: v for k, v in record.items() if k not in NESTED}
        for group in NESTED:
            for key, value in (record.get(group) or {}).items():
                flat[f"{group}_{key}"] = value
        rows.append(flat)
    frame = pd.DataFrame(rows)
    if "timestamp" in frame:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    return frame


def summarize(df: pd.DataFrame, by: list[str] | None = None,
              metrics: list[str] | None = None) -> pd.DataFrame:
    """n, median, min and max for each metric, grouped by `by`.

    n counts the runs that actually measured the metric, so a column with
    nulls reports a smaller n rather than a silently shifted median.
    """
    if df.empty:
        return pd.DataFrame()
    by = by or ["cell"]
    by = [column for column in by if column in df.columns]
    chosen = []
    numeric: dict[str, pd.Series] = {}
    for column in (metrics or DEFAULT_METRICS):
        if column not in df.columns:
            continue
        # An all-null column arrives as object dtype; it is still a metric that
        # was simply never measured, and it must report n = 0 rather than vanish.
        values = pd.to_numeric(df[column], errors="coerce")
        if values.notna().any() or df[column].isna().all():
            chosen.append(column)
            numeric[column] = values
    if not chosen:
        return pd.DataFrame()
    df = df.assign(**numeric)

    if not by:
        frames = {"all": df}
    else:
        frames = {key: group for key, group in df.groupby(by, dropna=False)}

    rows = []
    for key, group in frames.items():
        keys = key if isinstance(key, tuple) else (key,)
        base = dict(zip(by, keys)) if by else {}
        base["runs"] = len(group)
        for metric in chosen:
            values = group[metric].dropna()
            base[f"{metric}_n"] = int(len(values))
            base[f"{metric}_median"] = float(values.median()) if len(values) else None
            base[f"{metric}_min"] = float(values.min()) if len(values) else None
            base[f"{metric}_max"] = float(values.max()) if len(values) else None
        rows.append(base)
    return pd.DataFrame(rows)


def tidy(df: pd.DataFrame, by: list[str] | None = None,
         metrics: list[str] | None = None) -> pd.DataFrame:
    """The same summary in long form: one row per group and metric."""
    wide = summarize(df, by=by, metrics=metrics)
    if wide.empty:
        return wide
    by = [column for column in (by or ["cell"]) if column in wide.columns]
    rows = []
    for _, row in wide.iterrows():
        for column in wide.columns:
            if not column.endswith("_median"):
                continue
            metric = column[: -len("_median")]
            rows.append({
                **{key: row[key] for key in by},
                "metric": metric,
                "n": row.get(f"{metric}_n"),
                "median": row.get(f"{metric}_median"),
                "min": row.get(f"{metric}_min"),
                "max": row.get(f"{metric}_max"),
            })
    return pd.DataFrame(rows)
