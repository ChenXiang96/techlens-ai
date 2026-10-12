"""Validated exports with hard-link no-clobber publication and manifest-last completion."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict

from techlens.configuration import ApplicationError
from techlens.models import AssessmentReport
from techlens.models import AssessmentRequest
from techlens.configuration import parse_model
from techlens.research_models import PlannerCandidate
from techlens.presentation import DEMO_NOTICE, DISCLAIMER, render_report, validated_report, safe_url


class ExportProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    synthetic: bool
    scenario: Literal["single", "comparison", "partial"] | None
    cost_basis: Literal["fictional_demo", "configured_estimates"]
    validation_scope: Literal["authoritative_state_integrity"] = "authoritative_state_integrity"
    disclaimer: str


class ReportExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["techlens-local-report-v1"] = "techlens-local-report-v1"
    mode: Literal["demo", "live"]
    provenance: ExportProvenance
    candidates: list[PlannerCandidate]
    report: AssessmentReport


def export_bytes(state, public, *, mode, scenario=None):
    report, catalog = validated_report(state, public)
    envelope = ReportExport(mode=mode, provenance=ExportProvenance(
        synthetic=mode == "demo", scenario=scenario,
        cost_basis="fictional_demo" if mode == "demo" else "configured_estimates",
        disclaimer=(DEMO_NOTICE + ". " if mode == "demo" else "") + DISCLAIMER),
        candidates=catalog, report=report)
    return {"report.json": public.json(envelope.model_dump(mode="json")).encode("utf-8"),
            "report.md": render_report(state, public, mode=mode, scenario=scenario).encode("utf-8")}


class ExportDestination:
    def __init__(self, path):
        self.path = Path(path).absolute()
        failed = False
        try:
            self.path.mkdir(mode=0o700)  # Existing directories are never adopted.
            self.identity = self.path.stat()
            with TemporaryDirectory(prefix=".probe-", dir=self.path) as directory:
                source, target = Path(directory) / "a", Path(directory) / "b"
                source.write_bytes(b"capability")
                os.link(source, target)
                try:
                    os.link(source, target)
                except FileExistsError:
                    pass
                else:
                    raise OSError("No-clobber capability unavailable")
        except OSError:
            failed = True
        if failed:
            raise ApplicationError("Output destination unavailable or no-clobber publication unsupported.", 6)

    def publish(self, files, public):
        failed = False
        try:
            if self.path.is_symlink() or not os.path.samestat(self.identity, self.path.stat()):
                raise OSError("Output directory identity changed")
            # Stage the entire bundle before creating any public filename.
            with TemporaryDirectory(prefix=".staging-", dir=self.path) as directory:
                stage = Path(directory)
                manifest = {"schema_version": "techlens-local-bundle-v1", "complete": True,
                            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
                all_files = {**files, "manifest.json": public.json(manifest).encode("utf-8")}
                for name, data in all_files.items():
                    if name not in {"report.json", "report.md", "manifest.json"}:
                        raise OSError("Invalid artifact name")
                    with (stage / name).open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                for name in all_files:  # manifest is deliberately last
                    os.link(stage / name, self.path / name)
        except OSError:
            failed = True
        if failed:
            raise ApplicationError("Export failed; bundle may be incomplete. No assessment was repeated.", 6)


def save_plan(path, plan, public):
    data = public.json(plan.model_dump(mode="json")).encode("utf-8")
    failed = False
    try:
        with Path(path).open("xb") as handle:
            handle.write(data)
    except OSError:
        failed = True
    if failed:
        raise ApplicationError("Plan file could not be exclusively created.", 6)


def save_request(path, request, public):
    """Stage validated request bytes, then publish once without overwriting."""
    request = parse_model(request.model_dump(), AssessmentRequest)
    public.check(request.model_dump(mode="json"))
    for candidate in request.candidates:
        safe_url(str(candidate.repository_url))
        if candidate.homepage is not None:
            safe_url(str(candidate.homepage))
    data = public.json(request.model_dump(mode="json")).encode("utf-8")
    if len(data) > 1_048_576:
        raise ApplicationError("Request exceeds the bounded JSON input size.")
    target = Path(path).absolute()
    failed = False
    try:
        with TemporaryDirectory(prefix=".request-", dir=target.parent) as directory:
            source = Path(directory) / "request.json"
            with source.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(source, target)
    except OSError:
        failed = True
    if failed:
        raise ApplicationError("Request could not be exclusively published in the existing parent directory.", 6)
