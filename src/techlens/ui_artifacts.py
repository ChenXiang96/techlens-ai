"""Atomic bounded in-memory downloads; never publishes to a filesystem path."""
import hashlib
import io
import json
import zipfile

from techlens.exporting import export_bytes
from techlens.ui_contracts import UIError

LIMITS = {"report.json": 2 * 1024**2, "report.md": 2 * 1024**2, "manifest.json": 8192, "bundle": 5 * 1024**2}


def artifacts(state, public, scenario):
    files = export_bytes(state, public, mode="demo", scenario=scenario)
    if set(files) != {"report.json", "report.md"}:
        raise UIError("Report downloads unavailable.", 503)
    manifest = {"schema_version": "techlens-local-bundle-v1", "complete": True,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files = {**files, "manifest.json": public.json(manifest).encode("utf-8")}
    if any(len(data) > LIMITS[name] for name, data in files.items()):
        raise UIError("Report downloads exceed the local size limit.", 503)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, data)
    bundle = stream.getvalue()
    if len(bundle) > LIMITS["bundle"]:
        raise UIError("Report downloads exceed the local size limit.", 503)
    # Independently re-read before publishing the complete set.
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        if archive.namelist() != list(files) or any(archive.read(name) != data for name, data in files.items()):
            raise UIError("Report bundle integrity failed.", 503)
        checked = json.loads(archive.read("manifest.json"))
        if checked != manifest:
            raise UIError("Report bundle integrity failed.", 503)
    return {**files, "bundle": bundle}
