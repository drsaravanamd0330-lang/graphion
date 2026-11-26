"""Shared helpers for the XML exporters."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from . import config

_schemas: dict[str, etree.XMLSchema] = {}


def schema(name: str) -> etree.XMLSchema:
    if name not in _schemas:
        _schemas[name] = etree.XMLSchema(etree.parse(str(config.SCHEMAS_DIR / name)))
    return _schemas[name]


def validate_file(path: Path, schema_file: str) -> dict:
    try:
        doc = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:
        return {"valid": False, "schema": schema_file, "errors": [f"not well-formed: {exc}"]}
    xs = schema(schema_file)
    ok = xs.validate(doc)
    errors = [f"line {e.line}: {e.message}" for e in xs.error_log] if not ok else []
    return {"valid": ok, "schema": schema_file, "errors": errors[:50]}


def date_parts(iso: str | None) -> tuple[str, str, str] | None:
    if not iso:
        return None
    y, m, d = iso.split("-")
    return y, m, d
