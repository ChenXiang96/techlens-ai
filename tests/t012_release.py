"""Offline release checks only; no provider client, service or assessment engine."""
from email.parser import BytesParser
import hashlib
import re
import stat
import tarfile
import tomllib
from pathlib import Path, PurePosixPath
import zipfile

VERSION = "0.1.0"
MAX_MEMBERS = 10000
MAX_BYTES = 256 * 1024**2
ROOT_FILES = {
    "README.md", "CONTRIBUTING.md", "LICENSE", "pyproject.toml", "uv.lock",
    ".gitignore", ".gitattributes",
}
SDIST_DIRECTORIES = ("src/techlens", "tests", "docs", "examples", "evals/t006", "qa/t011")
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}


class ReleaseCheckError(ValueError):
    """Fixed-message verification failure, not a provider/domain exception."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_name(name):
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise ReleaseCheckError("Invalid archive member name.")
    if "\\" in name or ":" in name or any(ord(c) < 32 for c in name):
        raise ReleaseCheckError("Unsafe archive member path.")
    clean = name.removesuffix("/")
    path = PurePosixPath(clean)
    parts = clean.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if path.is_absolute() or any(
        p in {"", ".", ".."} or p.endswith((".", " ")) or
        p.split(".", 1)[0].upper() in reserved for p in parts
    ):
        raise ReleaseCheckError("Unsafe archive member path.")
    return path.as_posix()


def archive_payloads(path):
    """Validate every member before returning bounded file payloads for inspection."""
    path = Path(path)
    payloads, seen, total = {}, set(), 0

    def admit(name, size):
        nonlocal total
        name = checked_name(name)
        if name in seen:
            raise ReleaseCheckError("Duplicate archive member.")
        seen.add(name)
        total += size
        if size < 0 or len(seen) > MAX_MEMBERS or total > MAX_BYTES:
            raise ReleaseCheckError("Archive exceeds inspection bounds.")
        return name

    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                name = admit(member.filename, member.file_size)
                mode = member.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
                    raise ReleaseCheckError("Unsupported archive member type.")
                if not member.is_dir():
                    payloads[name] = archive.read(member)
    else:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                name = admit(member.name, member.size)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ReleaseCheckError("Unsupported archive member type.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseCheckError("Archive member has no payload.")
                with stream:
                    data = stream.read(MAX_BYTES + 1)
                if len(data) != member.size:
                    raise ReleaseCheckError("Archive member size mismatch.")
                payloads[name] = data
    return payloads


def extract_sdist(path, destination):
    """No extractall, overwrite, links or fallback to the source checkout."""
    payloads = archive_payloads(path)
    prefix = f"techlens-{VERSION}"
    if not payloads or any(not name.startswith(prefix + "/") for name in payloads):
        raise ReleaseCheckError("Unexpected source archive root.")
    destination = Path(destination)
    if destination.exists():
        raise ReleaseCheckError("Extraction destination must be new.")
    destination.mkdir(parents=True)
    for name, data in payloads.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ReleaseCheckError("Unsafe extraction target.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
    return destination / prefix


def source_included(name):
    parts = PurePosixPath(name).parts
    return not any(p in FORBIDDEN_PARTS for p in parts) and not name.endswith(
        (".pyc", ".pyo")
    ) and (name in ROOT_FILES or any(
        name.startswith(directory + "/") for directory in SDIST_DIRECTORIES
    ))


def source_payloads(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
            and source_included(p.relative_to(root).as_posix())}


def runtime_payloads(root):
    root = Path(root)
    return {"techlens/" + p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
            and (p.suffix == ".py" or p.parent.name == "ui_assets")}


def inspect_wheel(path, source_root, *, require_license=True):
    payloads = archive_payloads(path)
    expected = runtime_payloads(Path(source_root) / "src/techlens")
    actual = {name: data for name, data in payloads.items() if name.startswith("techlens/")}
    if not expected or actual != expected:
        raise ReleaseCheckError("Wheel runtime payload mismatch.")
    metadata_root = f"techlens-{VERSION}.dist-info/"
    if any(not name.startswith(("techlens/", metadata_root)) for name in payloads):
        raise ReleaseCheckError("Unexpected wheel payload.")
    metadata = BytesParser().parsebytes(payloads[metadata_root + "METADATA"])
    project = tomllib.loads((Path(source_root) / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if metadata["Name"] != "techlens" or metadata["Version"] != VERSION or metadata["Requires-Python"] != ">=3.11":
        raise ReleaseCheckError("Wheel identity metadata mismatch.")
    def requirement_key(value):
        from packaging.requirements import Requirement
        requirement = Requirement(value)
        return (requirement.name.lower(), str(requirement.specifier),
                tuple(sorted(requirement.extras)), str(requirement.marker))
    if {requirement_key(v) for v in metadata.get_all("Requires-Dist", [])} != {
        requirement_key(v) for v in project["dependencies"]
    }:
        raise ReleaseCheckError("Wheel runtime dependency mismatch.")
    if b"techlens = techlens.cli:main" not in payloads[metadata_root + "entry_points.txt"]:
        raise ReleaseCheckError("Wheel entry point mismatch.")
    if require_license:
        license_data = (Path(source_root) / "LICENSE").read_bytes()
        if metadata["License-Expression"] != "MIT" or metadata.get_all("License-File") != ["LICENSE"]:
            raise ReleaseCheckError("Wheel license metadata mismatch.")
        if payloads.get(metadata_root + "licenses/LICENSE") != license_data:
            raise ReleaseCheckError("Wheel license payload mismatch.")
    return {"members": {name: {"size": len(data), "sha256": digest(data)}
                        for name, data in sorted(payloads.items())},
            "runtime_members": len(actual), "sha256": digest(Path(path).read_bytes())}


def inspect_sdist(path, source_root):
    payloads = archive_payloads(path)
    prefix = f"techlens-{VERSION}/"
    if any(not name.startswith(prefix) for name in payloads):
        raise ReleaseCheckError("Unexpected source archive root.")
    stripped = {name[len(prefix):]: data for name, data in payloads.items()}
    package_metadata = stripped.pop("PKG-INFO", None)
    if package_metadata is None or stripped != source_payloads(source_root):
        raise ReleaseCheckError("Source archive inclusion or payload mismatch.")
    metadata = BytesParser().parsebytes(package_metadata)
    if metadata["Name"] != "techlens" or metadata["Version"] != VERSION:
        raise ReleaseCheckError("Source archive identity mismatch.")
    if metadata["License-Expression"] != "MIT" or metadata.get_all("License-File") != ["LICENSE"]:
        raise ReleaseCheckError("Source archive license mismatch.")
    return {"members": {name: {"size": len(data), "sha256": digest(data)}
                        for name, data in sorted(payloads.items())},
            "sha256": digest(Path(path).read_bytes())}
