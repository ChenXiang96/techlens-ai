"""Bounded saved-file integrity checks, NOT authoritative workflow validation.

Only the checked original JSON values enter the immutable projection. Pydantic is
an additional type check, never a source of replacement/defaulted display values.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import base64
import binascii
import hashlib
from io import BytesIO
import json
import math
import re
import stat
import struct
from time import monotonic
import zipfile
import zlib

from techlens.analysis_models import RUBRIC, SECTION_TITLES
from techlens.exporting import ReportExport
from techlens.models import TechnologyCandidate
from techlens.presentation import safe_url
from techlens.reporting import SUMMARIES

LIMITS = {"report.json": 2 * 1024**2, "report.md": 2 * 1024**2, "manifest.json": 8192}
ZIP_LIMIT = 5 * 1024**2
PROJECTION_LIMIT = 4 * 1024**2
NOTICE = ("Imported saved report — read-only. File hashes and supported report structure checked. "
          "Imported snapshot; origin is not authenticated. Original workflow validation and factual "
          "support were not repeated. Bundled Markdown is hash-checked, not rendered or compared for meaning.")


class SavedReportError(Exception):
    """Fixed diagnostic only; never retains files, parser errors or provider data."""


@dataclass(frozen=True)
class SavedReportSnapshot:
    projection: bytes


def _require(ok):
    if not ok:
        raise ValueError()


def _json(data):
    text = data.decode("utf-8", "strict")
    depth, quoted, escape = 0, False, False
    for c in text:
        if quoted:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c in "[{":
            depth += 1
            _require(depth <= 32)
        elif c in "]}":
            depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    def number(value):
        _require(len(value) <= 64)
        result = float(value)
        _require(math.isfinite(result))
        return result
    def integer(value):
        _require(len(value) <= 16)
        result = int(value)
        _require(abs(result) <= 9007199254740991)
        return result
    def invalid(_):
        raise ValueError()
    value = json.loads(text, object_pairs_hook=pairs, parse_float=number,
                       parse_int=integer, parse_constant=invalid)
    nodes = 0
    def bounded(item):
        nonlocal nodes
        nodes += 1
        _require(nodes <= 65536)
        if isinstance(item, (list, dict)):
            _require(len(item) <= 2048)
            for child in (list(item.keys()) + list(item.values()) if isinstance(item, dict) else item):
                bounded(child)
        elif isinstance(item, str):
            _require(len(item) <= 65536 and not any(0xD800 <= ord(c) <= 0xDFFF for c in item))
    bounded(value)
    _require(type(value) is dict)
    return value


def _wire(value, schema, root):
    """Require every emitted field, including null/default fields, without coercion."""
    if "$ref" in schema:
        return _wire(value, root["$defs"][schema["$ref"].rsplit("/", 1)[1]], root)
    choices = schema.get("anyOf", schema.get("oneOf"))
    if choices:
        valid = False
        for choice in choices:
            try:
                _wire(value, choice, root)
                valid = True
                break
            except ValueError:
                pass
        _require(valid)
        return
    if "const" in schema:
        _require(type(value) is type(schema["const"]) and value == schema["const"])
    if "enum" in schema:
        _require(value in schema["enum"])
    kind = schema.get("type")
    if kind == "object":
        _require(type(value) is dict and set(value) == set(schema.get("properties", {})))
        for key, item in value.items():
            _wire(item, schema["properties"][key], root)
    elif kind == "array":
        _require(type(value) is list)
        for item in value:
            _wire(item, schema["items"], root)
    elif kind == "string":
        _require(type(value) is str)
    elif kind == "integer":
        _require(type(value) is int)
    elif kind == "number":
        _require(type(value) in (int, float))
    elif kind == "boolean":
        _require(type(value) is bool)
    elif kind == "null":
        _require(value is None)


def _zip(data, deadline):
    _require(22 <= len(data) <= ZIP_LIMIT)
    # Supported producer subset: no prefix/trailer/comments, ZIP64 or multi-disk.
    eocd = struct.unpack("<4s4H2LH", data[-22:])
    _require(eocd[:5] == (b"PK\x05\x06", 0, 0, 3, 3) and eocd[7] == 0)
    central_size, central_offset = eocd[5:7]
    _require(central_offset + central_size == len(data) - 22 and central_size <= 4096)
    result, offset = {}, 0
    with zipfile.ZipFile(BytesIO(data)) as archive:
        entries = sorted(archive.infolist(), key=lambda x: x.header_offset)
        _require(len(entries) == 3)
        _require(sum(46 + len(i.orig_filename.encode("utf-8")) for i in entries) == central_size)
        for info in entries:
            _require(monotonic() <= deadline)
            name = info.orig_filename
            _require(name == info.filename and name in LIMITS and name not in result)
            _require(info.header_offset == offset and not info.extra and not info.comment)
            _require(info.flag_bits == 0 and info.compress_type in (0, 8))
            mode = info.external_attr >> 16
            _require(stat.S_IFMT(mode) in (0, stat.S_IFREG) and not info.is_dir() and not info.external_attr & 0x10)
            _require(info.file_size <= LIMITS[name] and info.compress_size <= ZIP_LIMIT)
            header = struct.unpack_from("<4s5H3L2H", data, offset)
            _require(header[0] == b"PK\x03\x04" and header[2] == info.flag_bits and
                     header[3] == info.compress_type and header[6:9] ==
                     (info.CRC, info.compress_size, info.file_size) and header[10] == 0)
            start = offset + 30 + header[9]
            _require(data[offset + 30:start] == name.encode("ascii"))
            end = start + info.compress_size
            _require(end <= central_offset)
            compressed = data[start:end]
            if info.compress_type == 8:
                decoder = zlib.decompressobj(-15)
                raw = decoder.decompress(compressed, LIMITS[name] + 1)
                _require(not decoder.unconsumed_tail and not decoder.unused_data and decoder.eof)
            else:
                raw = compressed
            _require(len(raw) == info.file_size and len(raw) <= LIMITS[name])
            _require(binascii.crc32(raw) & 0xffffffff == info.CRC)
            result[name], offset = raw, end
        _require(offset == central_offset)
    return result


def _files(submitted, deadline):
    _require(type(submitted) is dict and set(submitted) == {"format", "files"})
    _require(submitted["format"] in ("zip", "files") and type(submitted["files"]) is list)
    expected = 1 if submitted["format"] == "zip" else 3
    _require(len(submitted["files"]) == expected)
    result = {}
    for entry in submitted["files"]:
        _require(type(entry) is dict and set(entry) == {"name", "base64"})
        name, encoded = entry["name"], entry["base64"]
        _require(type(name) is str and type(encoded) is str)
        if expected == 1:
            _require(re.fullmatch(r"[^/\\:\x00-\x1f\x7f]{1,124}\.zip", name) is not None)
            limit = ZIP_LIMIT
        else:
            _require(name in LIMITS and name not in result)
            limit = LIMITS[name]
        _require(len(encoded) <= 4 * ((limit + 2) // 3))
        raw = base64.b64decode(encoded, validate=True)
        _require(len(raw) <= limit and base64.b64encode(raw).decode("ascii") == encoded)
        result[name] = raw
    return _zip(next(iter(result.values())), deadline) if expected == 1 else result


def _ids(items, prefix):
    _require(items == [f"{prefix}-{i:03d}" for i in range(1, len(items) + 1)])


def _unique(items):
    _require(len(items) == len(set(items)))


def _invariants(value):
    catalog, report, provenance = value["candidates"], value["report"], value["provenance"]
    cids = [c["candidate_id"] for c in catalog]
    _require(1 <= len(cids) <= 3)
    _ids(cids, "candidate")
    _require(report["candidate_ids"] == cids and report["candidate_names"] == [c["name"] for c in catalog])
    for candidate in catalog:
        TechnologyCandidate(name=candidate["name"], repository_url=candidate["repository_url"])
        safe_url(candidate["repository_url"])
    _require(provenance["synthetic"] == (value["mode"] == "demo"))
    _require(provenance["cost_basis"] == ("fictional_demo" if value["mode"] == "demo" else "configured_estimates"))
    _require(provenance["scenario"] is None or value["mode"] == "demo")
    _require(report["team_profile"] is not None and report["comparison"] is not None)
    _require(report["summary"] == SUMMARIES[report["status"]])
    claims, sources, flags = report["claims"], report["sources"], report["risk_flags"]
    _ids([s["evidence_id"] for s in sources], "evidence")
    _ids([c["claim_id"] for c in claims], "claim")
    _ids([f["flag_id"] for f in flags], "risk")
    source_map = {s["evidence_id"]: s for s in sources}
    claim_map = {c["claim_id"]: c for c in claims}
    for source in sources:
        _require(source["candidate_id"] is None or source["candidate_id"] in cids)
        safe_url(source["source_url"])  # Check only; preserve original spelling.
    for claim in claims:
        _require(bool(claim["evidence_ids"]) and bool(claim["statement"].strip()))
        _unique(claim["candidate_ids"])
        _unique(claim["evidence_ids"])
        _require(set(claim["candidate_ids"]) <= set(cids))
        _require(set(claim["evidence_ids"]) <= set(source_map))
        assigned = {source_map[e]["candidate_id"] for e in claim["evidence_ids"]} - {None}
        _require(assigned <= set(claim["candidate_ids"]))
        if len(claim["candidate_ids"]) > 1:
            _require(set(claim["candidate_ids"]) <= assigned)
    for flag in flags:
        _require(flag["candidate_id"] in cids)
        p = flag["provenance"]
        if p["kind"] == "evidence":
            _unique(p["evidence_ids"])
            _require(bool(p["evidence_ids"]) and set(p["evidence_ids"]) <= set(source_map))
            _require(all(source_map[e]["candidate_id"] in (None, flag["candidate_id"]) for e in p["evidence_ids"]))
        else:
            _require(p["operation_id"] == "github:" + flag["candidate_id"] or
                     re.fullmatch(r"web:web-\d{3}", p["operation_id"]) is not None)
    def dimensions(items, candidate=None):
        _require([d["dimension"] for d in items] == list(RUBRIC))
        for d in items:
            _unique(d["claim_ids"])
            _require(set(d["claim_ids"]) <= set(claim_map))
            _require((d["judgment"] == "insufficient_information") == (not d["claim_ids"]))
            if candidate:
                _require(all(claim_map[c]["candidate_ids"] == [candidate] for c in d["claim_ids"]))
    _require([a["candidate_id"] for a in report["analyses"]] == cids)
    for analysis in report["analyses"]:
        dimensions(analysis["dimensions"], analysis["candidate_id"])
    comparison = report["comparison"]
    _require(comparison["candidate_ids"] == cids)
    if len(cids) == 1:
        _require(comparison["status"] == "not_applicable" and not comparison["dimensions"])
    else:
        _require(comparison["status"] != "not_applicable")
        dimensions(comparison["dimensions"])
        for d in comparison["dimensions"]:
            _require(all(claim_map[c]["candidate_ids"] == cids for c in d["claim_ids"]))
    sections = report["sections"]
    _require(len(sections) == 13)
    limitations = report["research_notes"][1:]
    _require(bool(report["research_notes"]))
    used = set()
    for i, section in enumerate(sections, 1):
        _require(section["number"] == i and section["title"] == SECTION_TITLES[i - 1])
        refs = section["claim_ids"]
        _unique(refs)
        _require(set(refs) <= set(claim_map))
        used.update(refs)
        _require(section["risk_flag_ids"] == ([f["flag_id"] for f in flags] if i in (1, 9) else []))
        _require(section["limitation_ids"] == (limitations if i in (1, 13) else []))
        if i == 1:
            _require(refs == [c["claim_id"] for c in claims[:3]])
        elif i == 10:
            _require(refs == [c for d in comparison["dimensions"] for c in d["claim_ids"]])
        elif i == 11:
            _require(refs == [c for a in report["analyses"] for d in a["dimensions"] for c in d["claim_ids"]])
        elif i not in range(3, 9):
            _require(not refs)
        if i == 10 and len(cids) == 1:
            expected = ("not_applicable", "Comparison not applicable.")
        elif refs or i in (1, 2, 9, 12, 13):
            expected = ("available", "See referenced observations and claims.")
        else:
            expected = ("insufficient_information", "Insufficient information.")
        _require((section["status"], section["text"]) == expected)
    _require(used == set(claim_map))
    metrics = report["metrics"]
    if metrics is not None:
        _require(metrics["assessment_id"] == report["assessment_id"] and metrics["status"] == report["status"])
        for key, item in metrics.items():
            if type(item) is int:
                _require(0 <= item <= 9007199254740991)
        for key in ("settled_estimate_usd", "outstanding_reservations_usd"):
            item = metrics[key]
            _require(type(item) is str and len(item) <= 64 and Decimal(item).is_finite() and Decimal(item) >= 0)
        _require(metrics["llm_attempts"] == metrics["planner_attempts"] + metrics["analyzer_attempts"])
        _require(metrics["evidence_count"] == len(sources) and metrics["claim_count"] == len(claims))
        _require(metrics["research_rounds"] in (0, 1) and metrics["includes_human_wait"] is True)
        elapsed = metrics["elapsed_seconds"]
        if elapsed is not None:
            _require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0)
        # A metrics object exported after finalize_metrics has BOTH values.
        # The assembler's legitimate unmeasured case is metrics=null, not a
        # partially finalized object. Never fill missing measurements with zero.
        _require(metrics["finished_at"] is not None and elapsed is not None)
        start = datetime.fromisoformat(metrics["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(metrics["finished_at"].replace("Z", "+00:00"))
        _require(end >= start and abs((end - start).total_seconds() - elapsed) < 0.001)
    if report["status"] == "failed":
        # Fatal integrity failure cannot produce analysis claims. Existing
        # dimension/reference checks consequently require unknown judgments;
        # observations, risk provenance, notes and declared costs remain intact.
        _require(not claims)
    if report["status"] == "rejected":
        _require(not claims and not sources and not flags)
        _require(metrics is None or metrics["research_rounds"] == 0)


def _verify(submitted, public):
    deadline = monotonic() + 10
    bundle = _files(submitted, deadline)
    _require(set(bundle) == set(LIMITS))
    manifest = _json(bundle["manifest.json"])
    _require(set(manifest) == {"schema_version", "complete", "files"})
    _require(manifest["schema_version"] == "techlens-local-bundle-v1" and manifest["complete"] is True)
    _require(type(manifest["files"]) is dict and set(manifest["files"]) == {"report.json", "report.md"})
    for name, digest in manifest["files"].items():
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
        _require(hashlib.sha256(bundle[name]).hexdigest() == digest)
    public.check(bundle["report.md"].decode("utf-8", "strict"))
    public.check(bundle["manifest.json"].decode("utf-8", "strict"))
    original = _json(bundle["report.json"])
    public.check(original)
    schema = ReportExport.model_json_schema()
    _wire(original, schema, schema)
    ReportExport.model_validate(original)  # Discard coercions/defaults/URL normalization.
    _invariants(original)
    projection = public.json({"notice": NOTICE, "checks": {
        "file_hashes": "matched", "supported_structure": "checked",
        "origin_authenticated": False, "workflow_revalidated": False,
        "factual_support_verified": False}, "snapshot": original}).encode("utf-8")
    _require(len(projection) <= PROJECTION_LIMIT and monotonic() <= deadline)
    return SavedReportSnapshot(projection)


def verify_saved_report(submitted, public):
    """Shared ZIP/three-file boundary; no source object in the domain error frame."""
    result = None
    try:
        result = _verify(submitted, public)
    except Exception:
        pass
    submitted = public = None
    if result is None:
        # Outside the active except; the internal parser traceback is discarded.
        raise SavedReportError("Saved report file or supported structure check failed.")
    return result
