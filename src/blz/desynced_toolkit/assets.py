"""Asset access for the Desynced data extract, transparent to whether the source is an already-
extracted directory or an untouched game zip (e.g. ``main.zip``).

Nothing here requires pre-extracting the zip to disk: :class:`ZipSource` reads member bytes
straight out of the archive via the stdlib ``zipfile`` module. This is a Python-level
abstraction only -- Claude's own file tools (Read/Grep/Bash) still can't see inside a zip, so
extracting to a scratch directory is still the right move for interactive exploration. This
module is for the library at runtime, which never needs that.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol


class AssetSource(Protocol):
    """Read-only access to a package's files, keyed by posix-style relative path."""

    def read_text(self, path: str) -> str: ...
    def exists(self, path: str) -> bool: ...


@dataclass
class DirectorySource:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def read_text(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()


class ZipSource:
    """Reads member files directly out of a zip archive -- no extraction to disk.

    Handles the common case of the zip having every entry nested under a single top-level
    directory (e.g. ``Main/data/data.lua``) by detecting and stripping that prefix, so callers
    can use the same plain relative paths (``data/data.lua``) either way.
    """

    def __init__(self, zip_path: str | Path) -> None:
        self._zf = zipfile.ZipFile(zip_path)
        names = self._zf.namelist()
        self._prefix = self._detect_common_prefix(names)

    @staticmethod
    def _detect_common_prefix(names: list[str]) -> str:
        top_dirs = {n.split("/", 1)[0] for n in names if "/" in n}
        # a single shared top-level directory covering every entry -> strip it
        if len(top_dirs) == 1 and all(
            n.startswith(next(iter(top_dirs)) + "/") for n in names
        ):
            return next(iter(top_dirs)) + "/"
        return ""

    def _resolve(self, path: str) -> str:
        return self._prefix + path

    def read_text(self, path: str) -> str:
        # the zip's copy uses CRLF, the extracted directory's uses LF (confirmed byte-identical
        # otherwise) -- normalize so both sources produce identical strings
        return self._zf.read(self._resolve(path)).decode("utf-8").replace("\r\n", "\n")

    def exists(self, path: str) -> bool:
        try:
            self._zf.getinfo(self._resolve(path))
            return True
        except KeyError:
            return False


def open_asset_source(location: str | Path) -> AssetSource:
    """Auto-detects a directory vs. a zip file and returns the matching source."""
    p = Path(location)
    if p.is_dir():
        return DirectorySource(p)
    if zipfile.is_zipfile(p):
        return ZipSource(p)
    raise ValueError(f"{location!r} is neither a directory nor a zip file")


@dataclass
class PackageManifest:
    package_id: str
    entry: str
    dependencies: list[str]

    @property
    def entry_dir(self) -> str:
        return str(PurePosixPath(self.entry).parent)


def read_def_json(source: AssetSource, path: str = "def.json") -> dict:
    return json.loads(source.read_text(path))


def get_package_manifest(
    source: AssetSource, package_id: str, def_path: str = "def.json"
) -> PackageManifest:
    manifest = read_def_json(source, def_path)
    pkg = manifest["packages"][package_id]
    return PackageManifest(
        package_id=package_id,
        entry=pkg["entry"],
        dependencies=pkg.get("dependencies", []),
    )


def resolve_include(entry_dir: str, include: str) -> str:
    """`package.includes` entries are relative to the entry file's own directory."""
    return str(PurePosixPath(entry_dir) / include)
