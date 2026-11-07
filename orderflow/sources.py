"""Source readers. They yield `(line_number, record, raw_text, error)` and never raise on a
single bad row; file-level problems (missing required CSV columns, bad encoding) raise
SourceSchemaError / UnicodeDecodeError and fail the whole source before anything is loaded.

`line_number` is the PHYSICAL line in the file (CSV header = line 1), so a reject can be
located with any text editor.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterator, Optional, Sequence, Tuple

Item = Tuple[int, Optional[dict], str, Optional[Tuple[str, str]]]


class SourceSchemaError(Exception):
    """The file's structure is not what the contract says (e.g. a required column is gone)."""


class CsvSource:
    def __init__(self, path: Path, required: Sequence[str], optional: Sequence[str] = ()):
        self.path, self.required, self.optional = Path(path), list(required), list(optional)
        self.warnings: list[str] = []

    def __iter__(self) -> Iterator[Item]:
        # utf-8-sig: tolerate a BOM written by Excel
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:  # zero-byte file == empty batch, not an error
                self.warnings.append("EMPTY_INPUT: file has no content")
                return
            header = [h.strip() for h in header]
            if len(set(header)) != len(header):
                raise SourceSchemaError(f"duplicate column names in header: {header}")
            missing = [c for c in self.required if c not in header]
            if missing:
                raise SourceSchemaError(
                    f"missing required column(s) {missing}; got header {header}")
            extra = [c for c in header if c not in self.required and c not in self.optional]
            if extra:  # additive change is tolerated but surfaced
                self.warnings.append(f"UNEXPECTED_COLUMNS_IGNORED: {extra}")
            n = 0
            for row in reader:
                if not row:
                    continue
                n += 1
                raw = json.dumps(row, ensure_ascii=False)
                if len(row) != len(header):
                    yield (reader.line_num, None, raw,
                           ("MALFORMED_RECORD",
                            f"expected {len(header)} columns, found {len(row)}"))
                    continue
                yield (reader.line_num, dict(zip(header, row)), raw, None)
            if n == 0:
                self.warnings.append("EMPTY_INPUT: header only, no data rows")


class JsonlSource:
    def __init__(self, path: Path, known_fields: Sequence[str]):
        self.path, self.known = Path(path), set(known_fields)
        self.warnings: list[str] = []
        self._unknown: set[str] = set()

    def __iter__(self) -> Iterator[Item]:
        n = 0
        with open(self.path, encoding="utf-8-sig") as f:
            for line_number, line in enumerate(f, start=1):
                text = line.rstrip("\r\n")
                if not text.strip():
                    continue
                n += 1
                try:
                    rec = json.loads(text)
                except json.JSONDecodeError as exc:
                    yield (line_number, None, text, ("MALFORMED_RECORD", f"invalid JSON: {exc.msg}"))
                    continue
                if not isinstance(rec, dict):
                    yield (line_number, None, text,
                           ("MALFORMED_RECORD", "JSON value is not an object"))
                    continue
                self._unknown |= set(rec) - self.known
                yield (line_number, rec, text, None)
        if n == 0:
            self.warnings.append("EMPTY_INPUT: no records")
        if self._unknown:
            self.warnings.append(f"UNEXPECTED_FIELDS_IGNORED: {sorted(self._unknown)}")
