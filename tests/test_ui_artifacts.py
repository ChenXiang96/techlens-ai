import hashlib
import io
import json
import zipfile
import pytest

from techlens.exporting import export_bytes
from techlens.ui_coordinator import Coordinator
from ui_support import Scheduler, action, start


@pytest.mark.parametrize("scenario", ["single", "comparison", "partial"])
def test_exact_canonical_bundle_hashes_and_repeatability(scenario):
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    try:
        owner = c.owner(create=True)
        rid, _ = start(c, owner, scheduler, scenario)
        c.act(owner, "approve", rid, action(c, owner, "approve", rid))
        scheduler.finish()
        run = c.runs[rid]
        canonical = export_bytes(run.state, c.public, mode="demo", scenario=scenario)
        assert {k: run.downloads[k] for k in canonical} == canonical
        with zipfile.ZipFile(io.BytesIO(run.downloads["bundle"])) as archive:
            assert archive.namelist() == ["report.json", "report.md", "manifest.json"]
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest == {"schema_version": "techlens-local-bundle-v1", "complete": True,
                "files": {n: hashlib.sha256(b).hexdigest() for n, b in canonical.items()}}
            assert all(archive.read(n) == b for n, b in canonical.items())
        from techlens.ui_artifacts import artifacts
        assert artifacts(run.state, c.public, scenario) == run.downloads
    finally:
        c.close()


def test_failed_export_get_no_retry_explicit_mutation_once(monkeypatch):
    scheduler = Scheduler()
    c = Coordinator({}, scheduler=scheduler)
    try:
        owner = c.owner(create=True)
        rid, _ = start(c, owner, scheduler)
        from techlens import ui_coordinator
        original = ui_coordinator.artifacts
        def fail(*args):
            raise RuntimeError("private data")
        monkeypatch.setattr(ui_coordinator, "artifacts", fail)
        c.act(owner, "approve", rid, action(c, owner, "approve", rid))
        scheduler.finish()
        run = c.runs[rid]
        state = run.state
        assert run.phase == "terminal" and run.artifact_error and not run.downloads
        from techlens.ui_contracts import UIError
        with pytest.raises(UIError):
            c.download(owner, rid, "bundle")
        assert not scheduler.calls
        monkeypatch.setattr(ui_coordinator, "artifacts", original)
        run.session.start = run.session.resume = run.session.decision = fail
        submitted = action(c, owner, "artifacts", rid)
        receipt = c.act(owner, "artifacts", rid, submitted)
        assert c.act(owner, "artifacts", rid, submitted) == receipt
        assert len(scheduler.calls) == 1
        scheduler.finish()
        assert c.act(owner, "artifacts", rid, submitted)["status"] == "completed"
        assert run.state is state
        assert not run.artifact_error and run.downloads["bundle"]
        assert c.report(owner, rid)["report"]["status"] == "completed"
    finally:
        c.close()
