.PHONY: install test schemas

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m pytest

schemas:
	python3 -c "from pathlib import Path; from mailkit.cli.schemas import write_schema_files; write_schema_files(Path('schemas/v1'))"
