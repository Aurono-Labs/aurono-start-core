# aurono/events/schemas/dump_schemas.py

import json
from pathlib import Path

from aurono.events.schemas.registry import ALL_EVENT_SCHEMAS


OUT = Path(__file__).parent / "snapshots" / "event_schemas_v1.json"

def _type_repr(t):
    if isinstance(t, tuple):
        return [x.__name__ for x in t]
    return t.__name__


def dump():
    data = {}

    for name, schema in ALL_EVENT_SCHEMAS.items():
        data[name] = {
            "domain": schema.domain,
            "actor_types": sorted(schema.actor_types),
            "envelope": {
                "required": sorted(schema.envelope.required),
                "optional": sorted(schema.envelope.optional),
                "forbidden": sorted(schema.envelope.forbidden),
            },
            "payload": {
                k: _type_repr(schema.payload[k])
                for k in sorted(schema.payload)
            },
            "payload_optional": {
                k: _type_repr(schema.payload_optional[k])
                for k in sorted(schema.payload_optional)
            },
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    dump()
