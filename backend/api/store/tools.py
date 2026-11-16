"""Persistence adapter for calculators owned by tools.basic."""
from uuid import uuid4

from psycopg.types.json import Jsonb

from tools import basic
from tools.registry import http_catalog
from ..validation import utc_now

# Compatibility export; metadata is defined only in tools.registry.
TOOL_DEFINITIONS = http_catalog()


class ToolStore:
    def _record_calculation(self, name, payload, output):
        run_id = str(uuid4())
        self.run("INSERT INTO tool_runs VALUES(%s,%s,%s,%s,%s,%s)",
                 (run_id, name, output["version"], Jsonb(payload), Jsonb(output), utc_now()))
        return {"id": run_id, "name": name, **output}

    def calculate_emissions(self, payload: dict) -> dict:
        return self._record_calculation("calculate_emissions", payload, basic.calculate_emissions(payload))

    def voyage_time(self, payload: dict) -> dict:
        return self._record_calculation("voyage_time", payload, basic.voyage_time(payload))
