# aurono/events/schemas/verify_schemas.py

import json
import sys
from pathlib import Path

from aurono.events.schemas.registry import ALL_EVENT_SCHEMAS
from aurono.events.schemas.dump_schemas import _type_repr


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "event_schemas_v1.json"


def _current_schema_snapshot():
    out = {}

    for event_type, schema in ALL_EVENT_SCHEMAS.items():
        out[event_type] = {
            "domain": schema.domain,
            "actor_types": sorted(schema.actor_types),
            "envelope": {
                "required": sorted(schema.envelope.required),
                "optional": sorted(schema.envelope.optional),
                "forbidden": sorted(schema.envelope.forbidden),
            },
            "payload": {
                k: _type_repr(v) for k, v in schema.payload.items()
            },
            "payload_optional": {
                k: _type_repr(v) for k, v in schema.payload_optional.items()
            },
        }

    return out


def main():
    if not SNAPSHOT_PATH.exists():
        print("❌ No schema snapshot found.")
        print("Run dump_schemas.py first.")
        sys.exit(1)

    with open(SNAPSHOT_PATH) as f:
        frozen = json.load(f)

    current = _current_schema_snapshot()

    if frozen != current:
        print("❌ Event schema drift detected!")
        print("This change is NOT allowed without a new schema version.")
        sys.exit(1)

    print("✅ Event schemas verified — no drift detected.")


if __name__ == "__main__":
    main()
