"""Reproducible fetching of external artifacts pinned to a commit SHA.

Shared by every external-source fetcher in `scripts/`. Nothing here ever reads a
branch: a source is a repository plus one commit, and each download is recorded
in a manifest with its size, SHA256 and timestamp. A local file whose checksum
disagrees with the manifest aborts the run unless `--force` is given, so an
artifact can never be silently replaced under a pinned commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

RAW = "https://raw.githubusercontent.com"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Source:
    """One pinned external source and the files we take from it."""

    name: str
    repo: str
    commit: str
    paths: tuple[str, ...]
    note: str
    flatten: bool = True  # store as basenames rather than mirroring the repo tree

    @property
    def dest(self) -> Path:
        return ROOT / "data" / "external" / self.name

    @property
    def manifest_path(self) -> Path:
        return self.dest / "manifest.json"

    def url_for(self, path: str) -> str:
        return f"{RAW}/{self.repo}/{self.commit}/{path}"

    def key_for(self, path: str) -> str:
        return Path(path).name if self.flatten else path

    def local_for(self, path: str) -> Path:
        return self.dest / self.key_for(path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, timeout: float = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "table-iv-reproduction"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()


def load_manifest(source: Source) -> dict:
    if source.manifest_path.exists():
        return json.loads(source.manifest_path.read_text(encoding="utf-8"))
    return {"repo": source.repo, "commit": source.commit, "artifacts": {}}


def fetch(source: Source, *, force: bool = False, verify_only: bool = False) -> list[str]:
    """Fetch (or verify) every artifact of `source`. Returns a list of problems."""
    source.dest.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(source)
    if manifest.get("commit") != source.commit and manifest.get("artifacts") and not force:
        return [
            f"manifest was written for commit {manifest.get('commit')}, this fetcher "
            f"is pinned to {source.commit}; re-run with --force to replace"
        ]

    records = manifest["artifacts"]
    problems: list[str] = []

    for path in source.paths:
        key = source.key_for(path)
        target = source.local_for(path)
        recorded = records.get(key)

        if target.exists():
            digest = sha256(target.read_bytes())
            if recorded and recorded["sha256"] != digest:
                message = (
                    f"{key}: local checksum {digest[:16]}... differs from the "
                    f"manifest's {recorded['sha256'][:16]}..."
                )
                if not force:
                    problems.append(message + " (use --force to overwrite)")
                    print(f"  MISMATCH {message}")
                    continue
                print(f"  FORCED   {message} -- re-downloading")
            elif recorded:
                print(f"  OK       {key} ({recorded['size']} bytes, verified)")
                continue

        if verify_only:
            problems.append(f"{key}: absent or unrecorded, and --verify-only was given")
            print(f"  MISSING  {key}")
            continue

        try:
            payload = download(source.url_for(path))
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            problems.append(f"{key}: download failed -- {exc}")
            print(f"  FAILED   {key}: {exc}")
            continue

        digest = sha256(payload)
        if recorded and recorded["sha256"] != digest and not force:
            problems.append(
                f"{key}: upstream checksum {digest[:16]}... differs from the manifest "
                f"at the same commit -- refusing to overwrite (use --force)"
            )
            print(f"  CONFLICT {key}: upstream content changed at a pinned commit")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        records[key] = {
            "repo_path": path,
            "url": source.url_for(path),
            "commit": source.commit,
            "size": len(payload),
            "sha256": digest,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        print(f"  FETCHED  {key} ({len(payload)} bytes, sha256 {digest[:16]}...)")

    manifest.update(
        {
            "repo": source.repo,
            "commit": source.commit,
            "source": f"https://github.com/{source.repo}/tree/{source.commit}",
            "note": source.note,
            "artifacts": records,
        }
    )
    source.manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return problems


def main(source: Source, argv: list[str] | None = None, description: str = "") -> int:
    """Standard CLI for a one-source fetcher script."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a local artifact whose checksum differs from the manifest",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="re-check local files against the manifest without downloading",
    )
    args = parser.parse_args(argv)

    problems = fetch(source, force=args.force, verify_only=args.verify_only)
    print(f"\nManifest: {source.manifest_path}")
    if problems:
        print("\nProblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"All {len(source.paths)} artifacts present and verified at commit {source.commit}.")
    return 0
