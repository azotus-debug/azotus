"""JSON schema validation for approved.json artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

try:
    import jsonschema
except Exception:  # pragma: no cover - optional dependency
    jsonschema = None

APPROVED_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["start", "end", "text"],
                "properties": {
                    "id": {"type": ["integer", "string"]},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"},
                    "source_text": {"type": "string"},
                    "speaker": {"type": "string"},
                    "position": {"type": "string"},
                    "words": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["text", "start", "end"],
                            "properties": {
                                "text": {"type": "string"},
                                "start": {"type": "number"},
                                "end": {"type": "number"},
                                "speaker": {"type": "string"},
                            },
                            "additionalProperties": True,
                        },
                    },
                },
                "additionalProperties": True,
            },
        },
        "meta": {"type": "object"},
    },
    "additionalProperties": True,
}


def _basic_validate_approved_json(data: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["approved.json must be an object"]

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("approved.json must contain a non-empty 'segments' array")
        return errors

    for idx, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segments[{idx}] must be an object")
            continue

        for key in ("start", "end", "text"):
            if key not in segment:
                errors.append(f"segments[{idx}] missing required field '{key}'")

        start = segment.get("start")
        end = segment.get("end")
        text = segment.get("text")

        if not isinstance(text, str):
            errors.append(f"segments[{idx}].text must be a string")
        if not isinstance(start, (int, float)):
            errors.append(f"segments[{idx}].start must be a number")
        if not isinstance(end, (int, float)):
            errors.append(f"segments[{idx}].end must be a number")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end <= start:
            errors.append(f"segments[{idx}].end must be greater than start")

        words = segment.get("words")
        if words is not None:
            if not isinstance(words, list):
                errors.append(f"segments[{idx}].words must be an array")
            else:
                for w_idx, word in enumerate(words):
                    if not isinstance(word, dict):
                        errors.append(f"segments[{idx}].words[{w_idx}] must be an object")
                        continue
                    for key in ("text", "start", "end"):
                        if key not in word:
                            errors.append(f"segments[{idx}].words[{w_idx}] missing '{key}'")

    return errors


def validate_approved_json(data: Any) -> List[str]:
    """Return a list of validation errors; empty list means valid."""
    if jsonschema is None:
        return _basic_validate_approved_json(data)

    validator = jsonschema.Draft7Validator(APPROVED_JSON_SCHEMA)
    errors = []
    for error in validator.iter_errors(data):
        path = "/".join(str(p) for p in error.path)
        prefix = f"{path}: " if path else ""
        errors.append(f"{prefix}{error.message}")
    return errors


def validate_approved_json_file(path: Path | str) -> List[str]:
    """Load a file and validate it as approved.json; returns list of errors."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_approved_json(data)
