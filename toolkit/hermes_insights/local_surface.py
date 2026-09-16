"""Model-independent, read-only access to one explicitly configured local database."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import hashlib
from pathlib import Path
from zoneinfo import ZoneInfo

from . import hermes_surface, runtime
from .hermes_surface import SurfaceBinding, execute_bound
from .provenance import sha256_id


SURFACE_CONTRACT = "openhealthatlas-surface-v1"


@dataclass(frozen=True)
class LocalSurface:
    """Immutable instance settings; tool requests cannot select their own database."""

    database: str | Path
    timezone: str = "UTC"

    def __post_init__(self):
        database = Path(self.database).expanduser().resolve(strict=True)
        if not database.is_file():
            raise ValueError("Local database must be an existing file")
        ZoneInfo(self.timezone)
        object.__setattr__(self, "database", str(database))

    def execute(self, request: object) -> dict:
        binding = SurfaceBinding(
            str(self.database), SURFACE_CONTRACT, "personal", "dataset_id",
            sha256_id({"contract": "openhealthatlas-local-dataset-v1",
                       "database": str(self.database)}),
        )
        clock = partial(runtime.now, timezone=ZoneInfo(self.timezone))
        context = runtime.adapter_context(clock=clock, timezone=self.timezone)
        engine_id = sha256_id({
            "shared_engine": hermes_surface._engine_id(),
            "local_surface_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        })
        return execute_bound(request, binding=binding, context=context, engine_id=engine_id)
