"""Producer fixtures are built separately from import-only execution sentinels."""
import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
from io import BytesIO
import json
import struct
import zipfile

import pytest

from techlens.budget import BudgetLedger
from techlens.demo import demo_request
from techlens.exporting import export_bytes
from techlens.presentation import PublicOutput
from techlens.reporting import assemble_report
from techlens.reporting import SUMMARIES
from techlens.saved_reports import SavedReportError, verify_saved_report, LIMITS
from techlens.ui_coordinator import Coordinator
from ui_support import Scheduler, action, start


def bundle_files(files):
    manifest = {"schema_version": "techlens-local-bundle-v1", "complete": True,
                "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    return {**files, "manifest.json": json.dumps(manifest).encode()}


@lru_cache(maxsize=5)
def exported(status="completed"):
    """Unchanged real workflow/exporter, not an import validation imitation."""
    if status == "failed":
        state = {"request": demo_request("single"), "fatal_failure": "collection_integrity",
                 "current_round": 0, "errors": ["collection_integrity"],
                 "budget_ledger": BudgetLedger(run_id="failed", ceiling_usd="0.15")}
        state["report"] = assemble_report(state)
        return bundle_files(export_bytes(state, PublicOutput({}), mode="live"))
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    try:
        owner = c.owner(create=True)
        rid, _ = start(c, owner, scheduler, "partial" if status == "partial" else
                       "comparison" if status == "comparison" else "single")
        op = "reject" if status == "rejected" else "approve"
        c.act(owner, op, rid, action(c, owner, op, rid))
        scheduler.finish()
        return {n: raw for n, raw in c.runs[rid].downloads.items() if n != "bundle"}
    finally:
        c.close()


def input_files(files, format="files"):
    if format == "zip":
        out = BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        files = {"saved.zip": out.getvalue()}
    return {"format": format, "files": [{"name": name, "base64": base64.b64encode(raw).decode()}
                                          for name, raw in files.items()]}


def changed(mutator, status="completed"):
    files = exported(status)
    value = json.loads(files["report.json"])
    mutator(value)
    return bundle_files({"report.json": json.dumps(value, ensure_ascii=True).encode(), "report.md": files["report.md"]})


@pytest.mark.parametrize("status", ["completed", "comparison", "partial", "rejected", "failed"])
@pytest.mark.parametrize("format", ["files", "zip"])
def test_actual_export_compatibility_original_values_and_both_inputs(status, format):
    files = exported(status)
    snap = verify_saved_report(input_files(files, format), PublicOutput({}))
    result = json.loads(snap.projection)
    assert result["snapshot"] == json.loads(files["report.json"])
    assert not result["checks"]["origin_authenticated"]
    assert not result["checks"]["workflow_revalidated"]
    assert "read-only" in result["notice"]
    assert len(result["snapshot"]["report"]["sections"]) == 13
    if status == "failed":
        assert result["snapshot"]["report"]["metrics"] is None


def test_original_url_whitespace_decimal_and_markdown_are_not_rewritten():
    def mutate(v):
        v["candidates"][0]["repository_url"] = "https://github.com/Example/Repo.git/"
        v["report"]["sources"][0]["source_url"] = "https://EXAMPLE.com:443/a%2Fb?q=%2f#Part"
        v["report"]["claims"][0]["statement"] = "  <script>inert()</script>  "
        v["report"]["metrics"]["settled_estimate_usd"] = "0.01000"
    files = changed(mutate)
    files = bundle_files({"report.json": files["report.json"], "report.md": b"<script>different inert MD</script>"})
    value = json.loads(verify_saved_report(input_files(files), PublicOutput({})).projection)["snapshot"]
    assert value == json.loads(files["report.json"])
    assert b"different inert MD" not in verify_saved_report(input_files(files), PublicOutput({})).projection


@pytest.mark.parametrize("mutator", [
    lambda v: v.pop("mode"), lambda v: v["provenance"].pop("scenario"),
    lambda v: v["report"].pop("metrics"), lambda v: v.update(extra="not emitted"),
    lambda v: v["report"]["sections"][0].update(number=True),
    lambda v: v["report"]["sections"][2].update(status="available"),
    lambda v: v["report"]["sections"][0].update(title="Wrong"),
    lambda v: v["report"]["claims"][0].update(evidence_ids=["evidence-999"]),
    lambda v: v["report"]["claims"][0].update(candidate_ids=["candidate-999"]),
    lambda v: v["report"]["claims"][0].update(evidence_ids=[]),
    lambda v: v["report"]["sources"][0].update(source_url="https://user:password@example.com/"),
    lambda v: v["report"]["sources"][0].update(source_url="javascript:alert(1)"),
    lambda v: v["report"]["metrics"].update(status="running"),
    lambda v: v["report"]["metrics"].update(claim_count=999),
    lambda v: v["report"]["metrics"].update(llm_attempts=999),
    lambda v: v["report"]["metrics"].update(settled_estimate_usd="NaN"),
    lambda v: v["report"]["metrics"].update(settled_estimate_usd=0.01),
    lambda v: v["report"]["analyses"][0]["dimensions"][0].update(judgment="favorable", claim_ids=[]),
    lambda v: v["report"].update(candidate_ids=["candidate-999"]),
    lambda v: v["candidates"][0].update(repository_url="https://gitlab.com/x/y"),
])
def test_wire_and_represented_invariant_mutations_rejected(mutator):
    with pytest.raises(SavedReportError) as caught:
        verify_saved_report(input_files(changed(mutator)), PublicOutput({}))
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert str(caught.value) == "Saved report file or supported structure check failed."


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'\xff', b'{"x":NaN}', b'{"x":1e999}',
                                    b'{"x":' + b'[' * 33 + b'0' + b']' * 33 + b'}'])
def test_strict_json_and_hash_checks(raw):
    files = bundle_files({"report.json": raw, "report.md": b"text"})
    with pytest.raises(SavedReportError):
        verify_saved_report(input_files(files), PublicOutput({}))


@pytest.mark.parametrize("damage", ["hash", "incomplete", "version", "extra", "missing", "secret", "base64", "oversize"])
def test_bundle_integrity_and_secret_failures(damage):
    files = dict(exported())
    if damage == "hash":
        files["report.md"] += b"changed"
    elif damage in ("incomplete", "version"):
        manifest = json.loads(files["manifest.json"])
        manifest["complete" if damage == "incomplete" else "schema_version"] = False if damage == "incomplete" else "v9"
        files["manifest.json"] = json.dumps(manifest).encode()
    elif damage == "extra":
        files["unexpected.txt"] = b"extra"
    elif damage == "missing":
        del files["manifest.json"]
    elif damage == "secret":
        files = bundle_files({"report.json": files["report.json"], "report.md": b"FAKE-SAVED-SECRET"})
    elif damage == "oversize":
        files["report.md"] = b"a" * (LIMITS["report.md"] + 1)
    body = input_files(files)
    if damage == "base64":
        body["files"][0]["base64"] += "\n"
    with pytest.raises(SavedReportError) as caught:
        verify_saved_report(body, PublicOutput({"OPENAI_API_KEY": "FAKE-SAVED-SECRET"}))
    assert "FAKE-SAVED-SECRET" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("damage", ["duplicate", "nested", "backslash", "absolute", "drive", "extra",
    "symlink", "encrypted", "compression", "crc", "header", "overlap", "trailer", "bomb", "eof"])
def test_actual_zip_headers_ranges_decompression_and_crc(damage):
    files = dict(exported())
    if damage == "bomb":
        files["report.md"] = b"a" * (LIMITS["report.md"] + 1)
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            if name == "report.json":
                name = {"nested": "x/report.json", "backslash": "x\\report.json", "absolute": "/report.json",
                        "drive": "C:report.json"}.get(damage, name)
            entry = zipfile.ZipInfo(name)
            entry.compress_type = zipfile.ZIP_DEFLATED
            if damage == "symlink" and name == "report.json":
                entry.external_attr = 0o120777 << 16
            archive.writestr(entry, raw)
        if damage in ("duplicate", "extra"):
            with pytest.warns(UserWarning) if damage == "duplicate" else __import__("contextlib").nullcontext():
                archive.writestr("report.json" if damage == "duplicate" else "extra", b"x")
    raw = bytearray(out.getvalue())
    central = raw.index(b"PK\x01\x02")
    if damage == "bomb":
        # Lie in BOTH headers: actual expansion, not advertised size, must stop.
        local = raw.index(b"PK\x03\x04", 4)
        second = raw.index(b"PK\x01\x02", central + 4)
        struct.pack_into("<L", raw, local + 22, 10)
        struct.pack_into("<L", raw, second + 24, 10)
    elif damage == "encrypted":
        struct.pack_into("<H", raw, central + 8, 1)
    elif damage == "compression":
        struct.pack_into("<H", raw, central + 10, 12)
    elif damage == "crc":
        raw[central + 16] ^= 1
        raw[14] ^= 1  # Agreeing headers, wrong actual CRC.
    elif damage == "header":
        raw[0] = 0
    elif damage == "overlap":
        struct.pack_into("<L", raw, central + 42, 1)
    elif damage == "trailer":
        raw.extend(b"trailer")
    elif damage == "eof":
        size = struct.unpack_from("<L", raw, 18)[0]
        name_size = struct.unpack_from("<H", raw, 26)[0]
        raw[30 + name_size + size - 1] ^= 255
    body = input_files({"bad.zip": bytes(raw)})
    body["format"] = "zip"
    with pytest.raises(SavedReportError):
        verify_saved_report(body, PublicOutput({}))


def test_import_only_calls_no_session_graph_ledger_authorization_or_provider(monkeypatch):
    files = exported()
    def forbidden(*args, **kwargs):
        raise AssertionError("Import attempted execution")
    for target in ("techlens.application.AssessmentSession.__init__", "techlens.graph.build_graph",
                   "techlens.budget.BudgetLedger.__init__", "techlens.openai_planner.OpenAIResearchPlanner.__init__",
                   "techlens.tavily_search.TavilySearchProvider.__init__",
                   "techlens.authorization.authorize_live_run"):
        monkeypatch.setattr(target, forbidden)
    for format in ("files", "zip"):
        assert verify_saved_report(input_files(files, format), PublicOutput({})).projection


def test_fixed_domain_error_frame_discards_internal_parser_and_upload():
    try:
        verify_saved_report({"untrusted": "private-upload-sentinel"}, PublicOutput({}))
    except SavedReportError as error:
        assert error.__context__ is None and error.__cause__ is None
        trace = error.__traceback__
        while trace.tb_frame.f_code.co_name != "verify_saved_report":
            trace = trace.tb_next
        assert trace.tb_next is None
        assert trace.tb_frame.f_locals == {"submitted": None, "public": None, "result": None}


@pytest.mark.parametrize("format", ["files", "zip"])
@pytest.mark.parametrize("finish,elapsed", [
    (None, -1.5), (None, None), (None, 0), ("start", None),
    ("start", -1.5), ("start", True), ("start", "0"),
    ("start", float("inf")), ("start", float("nan")), ("start", 1.5),
])
def test_elapsed_metrics_reject_invalid_and_unfinished_combinations(format, finish, elapsed):
    def mutate(v):
        metrics = v["report"]["metrics"]
        metrics.update(finished_at=metrics["started_at"] if finish == "start" else finish,
                       elapsed_seconds=elapsed)
    # Rehash only JSON. Markdown is deliberately unchanged, not a semantic oracle.
    files = changed(mutate)
    with pytest.raises(SavedReportError) as caught:
        verify_saved_report(input_files(files, format), PublicOutput({}))
    assert str(caught.value) == "Saved report file or supported structure check failed."
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("format", ["files", "zip"])
@pytest.mark.parametrize("elapsed", [0, 0.0])
def test_valid_finalized_zero_duration_is_preserved(format, elapsed):
    def mutate(v):
        metrics = v["report"]["metrics"]
        metrics.update(finished_at=metrics["started_at"], elapsed_seconds=elapsed)
    files = changed(mutate)
    original = json.loads(files["report.json"])
    assert json.loads(verify_saved_report(input_files(files, format), PublicOutput({})).projection)["snapshot"] == original


@pytest.mark.parametrize("format", ["files", "zip"])
@pytest.mark.parametrize("status", ["completed", "comparison"])
def test_completed_to_failed_claim_mutant_rejected(format, status):
    def mutate(v):
        v["report"].update(status="failed", summary=SUMMARIES["failed"])
        v["report"]["metrics"]["status"] = "failed"
    files = changed(mutate, status)
    assert json.loads(files["report.json"])["report"]["claims"]
    with pytest.raises(SavedReportError) as caught:
        verify_saved_report(input_files(files, format), PublicOutput({}))
    assert str(caught.value) == "Saved report file or supported structure check failed."
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def failed_with_observations():
    """Supported failed assembly/export with original observations and real fake accounting."""
    from techlens.analysis import unknown_analysis
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    try:
        owner = c.owner(create=True)
        rid, _ = start(c, owner, scheduler)
        c.act(owner, "approve", rid, action(c, owner, "approve", rid))
        scheduler.finish()
        state = dict(c.runs[rid].state)
        state.update(fatal_failure="collection_integrity", claims=[],
                     analysis_result=unknown_analysis(state["request"]),
                     errors=[*state["errors"], "collection_integrity"])
        report = assemble_report(state)
        metrics = state["run_metrics"].model_copy(update={"status": "failed", "claim_count": 0,
            "error_categories": sorted(set([o.error_category for o in state["github_outcomes"] + state["web_outcomes"]
                if o.error_category] + state["errors"]))})
        state["report"] = report.model_copy(update={"metrics": metrics})
        return bundle_files(export_bytes(state, PublicOutput({}), mode="demo", scenario="single"))
    finally:
        c.close()


@pytest.mark.parametrize("format", ["files", "zip"])
def test_exported_failed_observations_notes_and_accounting_preserved(format):
    files = failed_with_observations()
    original = json.loads(files["report.json"])
    report = original["report"]
    assert report["status"] == "failed" and not report["claims"]
    assert report["sources"] and report["risk_flags"] and "collection_integrity" in report["research_notes"]
    assert float(report["metrics"]["settled_estimate_usd"]) > 0
    assert all(d["judgment"] == "insufficient_information" for a in report["analyses"] for d in a["dimensions"])
    assert json.loads(verify_saved_report(input_files(files, format), PublicOutput({})).projection)["snapshot"] == original
