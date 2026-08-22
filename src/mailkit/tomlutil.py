"""Minimal TOML writer for Mailkit config. Reading uses stdlib tomllib."""

from __future__ import annotations

from typing import Any


def dumps(data: dict[str, Any]) -> str:
    chunks: list[str] = []
    tables: list[tuple[str, dict]] = []
    array_tables: list[tuple[str, list]] = []
    scalars: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            array_tables.append((key, value))
        else:
            scalars.append(f"{_format_key(key)} = {_encode(value)}")
    if scalars:
        chunks.extend(scalars)
        chunks.append("")
    for name, table in tables:
        _dump_table(chunks, name, table)
    for name, items in array_tables:
        for item in items:
            chunks.append(f"[[{name}]]")
            nested_tables: list[tuple[str, dict]] = []
            for key, value in item.items():
                if isinstance(value, dict):
                    nested_tables.append((f"{name}.{key}", value))
                else:
                    chunks.append(f"{_format_key(key)} = {_encode(value)}")
            chunks.append("")
            for nested_name, nested in nested_tables:
                _dump_table(chunks, nested_name, nested)
    return "\n".join(chunks).rstrip() + "\n"


def _dump_table(chunks: list[str], name: str, table: dict[str, Any]) -> None:
    nested: list[tuple[str, dict]] = []
    scalars: list[str] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested.append((f"{name}.{key}", value))
        else:
            scalars.append(f"{_format_key(key)} = {_encode(value)}")
    if scalars or not nested:
        chunks.append(f"[{name}]")
        chunks.extend(scalars)
        chunks.append("")
    for nested_name, nested_table in nested:
        _dump_table(chunks, nested_name, nested_table)


def _format_key(key: str) -> str:
    if key.replace("_", "").replace("-", "").isalnum() and key[0].isalpha():
        return key
    return _encode(key)


def _encode(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[ " + ", ".join(_encode(v) for v in value) + " ]"
    if value is None:
        return '""'
    return _encode_str(str(value))


def _encode_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'
