from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_registry():
    registry = Registry()
    schemas = []
    for path in SCHEMA_DIR.rglob("*.schema.json"):
        schema = load_json(path)
        Draft202012Validator.check_schema(schema)
        schemas.append((path, schema))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, schemas


def validate_example(registry, schema_path: str, example_path: str):
    schema = load_json(ROOT / schema_path)
    instance = load_json(ROOT / example_path)
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        formatted = "\n".join(
            f"- {'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise AssertionError(f"{example_path} failed validation:\n{formatted}")


def main():
    registry, schemas = build_registry()
    validate_example(registry, "schemas/protocol/handshake.schema.json", "examples/handshake-request.json")
    validate_example(registry, "schemas/protocol/handshake.schema.json", "examples/handshake-response.json")
    validate_example(registry, "schemas/operations/operation-plan.schema.json", "examples/operation-plan.json")
    print(f"Validated {len(schemas)} schemas and 3 examples successfully.")


if __name__ == "__main__":
    main()
