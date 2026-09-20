"""Environment-derived configuration.

Every path, host and port comes from a CLI flag or an environment variable.
No machine-specific value is committed to this repository.

    OMLX_BIN            path to the inference server binary (required to start one)
    OMLX_SRC            server source checkout, for the git sha
                        (default: two levels above the binary's directory)
    OMLX_MODEL_DIR      directory holding model weights (required to start one)
    OMLX_RESEARCH_BASE  server base path: settings, logs, cache (required)
    OMLX_HOST           bind/connect host          (default 127.0.0.1)
    OMLX_PORT           port                       (default 8011)
    OMLX_MEMORY_GUARD_GB memory guard in GB        (default 36)
    OMLX_SSD_CACHE_DIR  paged SSD cache directory  (default <base>/ssd-cache)
    OMLX_API_KEY        bearer token, only if the server requires one
    MODEL               model id served by the instance
    OMLX_MIN_FREE_PCT   preflight system-wide free-memory floor, percent (default 60)
    OMLX_MAX_WIRED_GB   preflight wired+compressor ceiling in GB (default 24)
    OMLX_MAX_FOREIGN_RSS_GB preflight foreign-process RSS ceiling in GB (default 20)
"""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8011
DEFAULT_MEMORY_GUARD_GB = 36
DEFAULT_MIN_FREE_PCT = 60.0
DEFAULT_MAX_WIRED_GB = 24.0
DEFAULT_MAX_FOREIGN_RSS_GB = 20.0
SERVER_PROCESS_NAME = "omlx-server"
INSTANCE_FILE = "research-instance.json"


def _env_path(name: str) -> pathlib.Path | None:
    value = os.environ.get(name)
    return pathlib.Path(value).expanduser() if value else None


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    bin: pathlib.Path | None
    src: pathlib.Path | None
    model_dir: pathlib.Path | None
    base_path: pathlib.Path | None
    host: str
    port: int
    memory_guard_gb: float
    ssd_cache_dir: pathlib.Path | None
    api_key: str | None
    model: str | None
    min_free_pct: float
    max_wired_gb: float
    max_foreign_rss_gb: float

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def instance_file(self) -> pathlib.Path | None:
        return self.base_path / INSTANCE_FILE if self.base_path else None

    @property
    def server_log(self) -> pathlib.Path | None:
        return self.base_path / "logs" / "server.log" if self.base_path else None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


def load(**overrides) -> Settings:
    """Build settings from the environment, with keyword overrides on top."""
    binary = _env_path("OMLX_BIN")
    src = _env_path("OMLX_SRC")
    if src is None and binary is not None:
        src = binary.parent.parent.parent
    base = _env_path("OMLX_RESEARCH_BASE")
    cache = _env_path("OMLX_SSD_CACHE_DIR")
    if cache is None and base is not None:
        cache = base / "ssd-cache"
    values = {
        "bin": binary,
        "src": src,
        "model_dir": _env_path("OMLX_MODEL_DIR"),
        "base_path": base,
        "host": os.environ.get("OMLX_HOST", DEFAULT_HOST),
        "port": int(os.environ.get("OMLX_PORT", DEFAULT_PORT)),
        "memory_guard_gb": _env_float("OMLX_MEMORY_GUARD_GB", DEFAULT_MEMORY_GUARD_GB),
        "ssd_cache_dir": cache,
        "api_key": os.environ.get("OMLX_API_KEY") or None,
        "model": os.environ.get("MODEL") or None,
        "min_free_pct": _env_float("OMLX_MIN_FREE_PCT", DEFAULT_MIN_FREE_PCT),
        "max_wired_gb": _env_float("OMLX_MAX_WIRED_GB", DEFAULT_MAX_WIRED_GB),
        "max_foreign_rss_gb": _env_float(
            "OMLX_MAX_FOREIGN_RSS_GB", DEFAULT_MAX_FOREIGN_RSS_GB
        ),
    }
    values.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**values)
