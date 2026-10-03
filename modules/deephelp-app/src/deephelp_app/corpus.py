"""Bounded, non-executing corpus readers. Preview all rows before any remote writes."""

import csv
import hashlib
import io
import json
import unicodedata
import zipfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from deephelp_app.domain.models import CorpusRecord

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
REQUIRED = {
    "doc_id",
    "content",
    "intent_code",
    "split",
    "source_group",
    "variant_group",
    "synthetic",
}


class CorpusFormatError(ValueError):
    """Static reader diagnostic that contains a position, never an input cell value."""


def normalized(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def content_hash(record: CorpusRecord) -> str:
    return digest(normalized(record.content))


@dataclass(frozen=True)
class CorpusPreview:
    file_sha256: str
    records: tuple[CorpusRecord, ...]
    errors: tuple[str, ...]

    def require_valid(self) -> None:
        if self.errors:
            raise ValueError("Corpus preview failed: " + "; ".join(self.errors))

    @property
    def index_records(self) -> tuple[CorpusRecord, ...]:
        return tuple(r for r in self.records if r.split in {"train", "reference"})

    @property
    def index_digest(self) -> str:
        return digest(
            [r.model_dump(mode="json") for r in sorted(self.index_records, key=lambda r: r.doc_id)]
        )

    def summary(self) -> dict[str, object]:
        return {
            "file_sha256": self.file_sha256,
            "index_digest": self.index_digest,
            "rows": len(self.records),
            "index_rows": len(self.index_records),
            "query_rows": len(self.records) - len(self.index_records),
            "errors": self.errors,
        }


def validate_records(rows: list[tuple[int, object]], file_sha256: str) -> CorpusPreview:
    errors: list[str] = []
    records: list[CorpusRecord] = []
    if len(rows) > MAX_ROWS:
        return CorpusPreview(file_sha256, (), (f"More than {MAX_ROWS} rows",))
    ids: set[str] = set()
    texts: dict[str, str] = {}
    groups: dict[tuple[str, str], str] = {}
    for line, raw in rows:
        try:
            record = CorpusRecord.model_validate(raw)
            if record.doc_id in ids:
                raise ValueError("Duplicate doc_id")
            text_hash = content_hash(record)
            if text_hash in texts:
                raise ValueError("Duplicate normalized content (including cross-split leakage)")
            for key in ("source_group", "variant_group"):
                group = (key, getattr(record, key))
                if group in groups and groups[group] != record.split:
                    raise ValueError("Source/variant group crosses splits")
            ids.add(record.doc_id)
            texts[text_hash] = record.split
            for key in ("source_group", "variant_group"):
                groups[(key, getattr(record, key))] = record.split
            records.append(record)
        except (ValueError, ValidationError) as exc:
            # Structural errors only: never echo invalid customer content or cell values.
            detail = (
                ", ".join(str(e["loc"]) + ": " + str(e["msg"]) for e in exc.errors())
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            errors.append(f"row {line}: {detail}")
    if not rows:
        errors.append("Empty corpus")
    return CorpusPreview(file_sha256, tuple(records), tuple(errors))


def _cell_values(data: bytes, mapping: dict[str, str]) -> list[tuple[int, object]]:
    """Read ONLY first worksheet, with explicit A/B/... mapping and no formula evaluation."""
    if not REQUIRED <= mapping.keys() or len(set(mapping.values())) != len(mapping):
        raise CorpusFormatError("XLSX requires unique explicit mappings for required columns")
    if any(not c.isalpha() or not c.isascii() or c != c.upper() for c in mapping.values()):
        raise CorpusFormatError("XLSX column names must be uppercase letters")
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        if len(book.infolist()) > 128 or sum(f.file_size for f in book.infolist()) > 8 * MAX_BYTES:
            raise CorpusFormatError("XLSX expanded size/entry limit exceeded")
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheets = workbook.findall("s:sheets/s:sheet", ns)
        if len(sheets) != 1:
            raise CorpusFormatError("XLSX adapter requires exactly one worksheet")
        rel_id = sheets[0].get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = [
            r.get("Target", "")
            for r in rels
            if r.get("Id") == rel_id and r.get("TargetMode") != "External"
        ]
        if len(targets) != 1:
            raise CorpusFormatError("Invalid worksheet relationship")
        target = targets[0].lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
        if ".." in sheet_path.split("/"):
            raise CorpusFormatError("Invalid worksheet path")
        strings: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
            strings = ["".join(si.itertext()) for si in shared]
        sheet = ET.fromstring(book.read(sheet_path))
        rows: list[tuple[int, object]] = []
        for number, row in enumerate(sheet.findall("s:sheetData/s:row", ns), 1):
            values: dict[str, str] = {}
            for cell in row.findall("s:c", ns):
                if cell.find("s:f", ns) is not None:
                    raise CorpusFormatError(f"row {number}: Formula cell rejected")
                column = "".join(c for c in cell.get("r", "") if c.isalpha())
                kind = cell.get("t")
                value = cell.findtext("s:v", "", ns)
                if kind == "s":
                    value = strings[int(value)]
                elif kind == "inlineStr":
                    inline = cell.find("s:is", ns)
                    if inline is None:
                        raise CorpusFormatError(f"row {number}: Missing inline text")
                    value = "".join(inline.itertext())
                elif column in mapping.values():
                    raise CorpusFormatError(f"row {number}: Mapped cells must be stored as text")
                values[column] = value
            rows.append(
                (number, _decode_strings({k: values.get(v, "") for k, v in mapping.items()}))
            )
    return rows


def _decode_strings(row: dict[str, str]) -> dict[str, object]:
    result: dict[str, object] = dict(row)
    result["synthetic"] = json.loads(row.get("synthetic", "false"))
    for key in ("metadata", "label_path"):
        if key in row:
            result[key] = json.loads(row[key]) if row[key] else ({} if key == "metadata" else None)
    return result


def read_corpus(path: Path, *, columns: dict[str, str] | None = None) -> CorpusPreview:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Corpus exceeds byte limit")
    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    rows: list[tuple[int, object]] = []
    errors: list[str] = []
    if path.suffix.lower() == ".xlsx":
        try:
            rows = _cell_values(data, columns or {})
        except CorpusFormatError as exc:
            return CorpusPreview(file_hash, (), (str(exc),))
        except ValueError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile:
            return CorpusPreview(
                file_hash,
                (),
                ("Invalid XLSX: explicit text columns required; formula/structure rejected",),
            )
    elif path.suffix.lower() == ".jsonl":
        for number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
            try:
                if not line.strip():
                    raise ValueError("Empty line")
                rows.append((number, json.loads(line)))
            except ValueError:
                errors.append(f"row {number}: Empty line or invalid JSON")
    elif path.suffix.lower() == ".csv":
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig")), strict=True)
        headers = next(reader, [])
        if not REQUIRED <= set(headers) or len(set(headers)) != len(headers):
            return CorpusPreview(file_hash, (), ("Missing/duplicate CSV headers",))
        for number, cells in enumerate(reader, 2):
            try:
                if len(cells) != len(headers):
                    raise ValueError("Missing/extra CSV cells")
                row = dict(zip(headers, cells, strict=True))
                if any(str(v).lstrip().startswith(("=", "+", "-", "@")) for v in row.values()):
                    raise ValueError("Formula-like CSV cell")
                rows.append((number, _decode_strings(row)))
            except ValueError:
                errors.append(f"row {number}: Missing, extra, formula-like or invalid JSON cell")
    else:
        raise ValueError("Supported corpus formats: JSONL, CSV, XLSX")
    preview = validate_records(rows, file_hash)
    return CorpusPreview(file_hash, preview.records, tuple(errors) + preview.errors)


def frozen_preview() -> CorpusPreview:
    from deephelp_app.evaluation.samples import load_corpus

    """M02 gold-null cases remain query-only and are evaluated separately by future classifiers."""
    cases = [c for c in load_corpus().cases if c.expected.intent is not None]
    raw = [
        {
            "doc_id": c.case_id,
            "content": c.message.raw_text,
            "intent_code": c.expected.intent,
            "split": c.split,
            "source_group": c.source_group,
            "variant_group": c.variant_group,
            "synthetic": True,
            "metadata": {"scenario": c.scenario},
        }
        for c in cases
    ]
    source = files("deephelp_app").joinpath("assets/evaluation/cases.json").read_bytes()
    return validate_records(list(enumerate(raw, 1)), hashlib.sha256(source).hexdigest())
