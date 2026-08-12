#!/usr/bin/env python3
"""Validate Trace2Skill invariants without third-party dependencies."""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any


def load_exporter():
    path = Path(__file__).with_name("export_agentteams_trace.py")
    spec = importlib.util.spec_from_file_location("trace_exporter", path)
    if not spec or not spec.loader:
        raise RuntimeError("Unable to load trace exporter module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _type_matches(instance: Any, expected: str) -> bool:
    mapping = {
        "object": lambda value: isinstance(value, dict),
        "array": lambda value: isinstance(value, list),
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "null": lambda value: value is None,
    }
    return mapping[expected](instance)


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"Only local schema references are supported: {reference}")
    node: Any = root
    for token in reference[2:].split("/"):
        node = node[token.replace("~1", "/").replace("~0", "~")]
    return node


def validate_schema_instance(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    root = root or schema
    if "$ref" in schema:
        return validate_schema_instance(instance, _resolve_ref(root, schema["$ref"]), root, path)
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    expected_type = schema.get("type")
    if expected_type:
        accepted = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_matches(instance, item) for item in accepted):
            return [f"{path}: expected type {accepted}, got {type(instance).__name__}"]
    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(f"{path}: missing required property {required!r}")
        if schema.get("additionalProperties") is False:
            for key in instance.keys() - properties.keys():
                errors.append(f"{path}: unexpected property {key!r}")
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_schema_instance(value, properties[key], root, f"{path}.{key}"))
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems")
        if "items" in schema:
            for index, value in enumerate(instance):
                errors.extend(validate_schema_instance(value, schema["items"], root, f"{path}[{index}]"))
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{path}: invalid date-time")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).parents[1] / "schemas" / "trace.schema.json",
    )
    args = parser.parse_args()

    trace = json.loads(args.trace.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    exporter = load_exporter()
    errors = validate_schema_instance(trace, schema)
    errors.extend(exporter.validate_trace(trace))
    if trace.get("schema_version") != schema["properties"]["schema_version"]["const"]:
        errors.append("trace and schema versions differ")
    if errors:
        for error in errors:
            print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(f"[PASS] {args.trace}")
    print(
        f"events={trace['metrics']['event_count']} "
        f"tools={trace['metrics']['tool_call_count']} "
        f"status={trace['run']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
