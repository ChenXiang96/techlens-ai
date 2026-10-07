import hashlib
import json
import os

import pytest

from techlens.configuration import ApplicationError
from techlens.exporting import ExportDestination, save_plan
from techlens.presentation import PublicOutput
from techlens.application import AssessmentSession, demo_dependencies
from techlens.demo import demo_request


def test_no_clobber_existing_directory(tmp_path):
    with pytest.raises(ApplicationError):
        ExportDestination(tmp_path)


def test_no_clobber_capability_failure_is_early(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("private-path")
    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(ApplicationError) as error:
        ExportDestination(tmp_path / "out")
    assert "private-path" not in str(error.value)
    assert error.value.__context__ is None


def test_prepared_files_and_hashed_manifest_last(tmp_path, monkeypatch):
    dest = ExportDestination(tmp_path / "out")
    linked = []
    original = os.link

    def link(source, target):
        assert (source.parent / "report.json").exists()
        assert (source.parent / "report.md").exists()
        assert (source.parent / "manifest.json").exists()
        linked.append(target.name)
        original(source, target)

    monkeypatch.setattr(os, "link", link)
    files = {"report.json": b"{}", "report.md": b"test"}
    dest.publish(files, PublicOutput({}))
    assert linked == ["report.json", "report.md", "manifest.json"]
    manifest = json.loads((dest.path / "manifest.json").read_text())
    assert manifest["complete"]
    for name, data in files.items():
        assert manifest["files"][name] == hashlib.sha256(data).hexdigest()


def test_raced_in_file_preserved_and_manifest_absent(tmp_path):
    dest = ExportDestination(tmp_path / "out")
    (dest.path / "report.md").write_text("other-owner")
    with pytest.raises(ApplicationError):
        dest.publish({"report.json": b"{}", "report.md": b"replacement"}, PublicOutput({}))
    assert (dest.path / "report.md").read_text() == "other-owner"
    assert not (dest.path / "manifest.json").exists()


def test_save_plan_no_overwrite_or_secret_write(tmp_path):
    session = AssessmentSession(demo_request("single"), demo_dependencies("single"))
    state = session.start()
    plan = state["research_plan"]
    path = tmp_path / "plan.json"
    save_plan(path, plan, PublicOutput({}))
    assert json.loads(path.read_text()) == plan.model_dump(mode="json")
    with pytest.raises(ApplicationError):
        save_plan(path, plan, PublicOutput({}))
    blocked = tmp_path / "blocked.json"
    with pytest.raises(ApplicationError):
        save_plan(blocked, plan, PublicOutput({"OPENAI_API_KEY": plan.objective}))
    assert not blocked.exists()


@pytest.mark.parametrize("failure", ["staging", "publish"])
def test_late_export_failure_does_not_repeat_graph(tmp_path, monkeypatch, failure):
    import io
    import techlens.cli as cli
    import techlens.exporting as exporting
    deps = demo_dependencies("single")
    monkeypatch.setattr(cli, "demo_dependencies", lambda _: deps)
    original = exporting.ExportDestination.publish

    def fail_publish(self, files, public):
        if failure == "staging":
            monkeypatch.setattr(os, "fsync", lambda *_: (_ for _ in ()).throw(OSError()))
        else:
            (self.path / "report.md").write_text("other")
        original(self, files, public)

    monkeypatch.setattr(exporting.ExportDestination, "publish", fail_publish)
    out = io.StringIO()
    code = cli.main(["demo", "--output-dir", str(tmp_path / "out")],
                    stdin=io.StringIO("approve\n"), stdout=out, environ={})
    assert code == 6
    assert deps.planner.generation_calls == deps.analyzer.generation_calls == deps.search.calls == 1
    assert len(deps.github.calls) == 1
    assert not (tmp_path / "out" / "manifest.json").exists()
