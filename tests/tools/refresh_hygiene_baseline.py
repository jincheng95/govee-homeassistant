"""Regenerate tests/hygiene_upstream_baseline.json from an upstream ref.

Usage: python tests/tools/refresh_hygiene_baseline.py [REF]   (default: upstream/main)

Reads every text blob of REF straight from the object store (no checkout),
runs the hygiene gate's raw collectors over it, and writes the SHA-256 of each
match per category together with the commit it came from.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tests.test_repo_hygiene import (  # noqa: E402
    BASELINE_COLLECTORS,
    BASELINE_PATH,
    BINARY_SUFFIXES,
    _scannable_lines,
    fingerprint,
)


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, check=True).stdout


def _text_blobs(commit: str) -> list[tuple[str, str]]:
    """(path, blob sha) for every regular file in ``commit`` the gate would read."""
    blobs = []
    for entry in _git("ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        meta, path = entry.decode().split("\t", 1)
        _mode, kind, sha = meta.split()
        if kind == "blob" and Path(path).suffix.lower() not in BINARY_SUFFIXES:
            blobs.append((path, sha))
    return blobs


def _read_blobs(shas: list[str]) -> list[bytes]:
    """Blob contents in order, via one ``git cat-file --batch`` process."""
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "--batch"],
        input="\n".join(shas).encode() + b"\n",
        capture_output=True,
        check=True,
    ).stdout
    contents, pos = [], 0
    for _ in shas:
        header_end = out.index(b"\n", pos)
        size = int(out[pos:header_end].split()[2])
        start = header_end + 1
        contents.append(out[start : start + size])
        pos = start + size + 1
    return contents


def build(ref: str) -> dict:
    """The baseline document for ``ref``."""
    commit = _git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    blobs = _text_blobs(commit)
    found: dict[str, set[str]] = {category: set() for category in BASELINE_COLLECTORS}
    for raw in _read_blobs([sha for _path, sha in blobs]):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for _lineno, line in _scannable_lines(text):
            for category, collect in BASELINE_COLLECTORS.items():
                found[category].update(fingerprint(category, value) for value in collect(line))
    return {
        "upstream_ref": ref,
        "upstream_commit": commit,
        "identifiers": {category: sorted(hashes) for category, hashes in found.items()},
    }


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "upstream/main"
    document = build(ref)
    BASELINE_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    counts = ", ".join(f"{category} {len(hashes)}" for category, hashes in document["identifiers"].items())
    print(f"{BASELINE_PATH.relative_to(REPO_ROOT)} <- {ref} {document['upstream_commit']}: {counts}")


if __name__ == "__main__":
    main()
