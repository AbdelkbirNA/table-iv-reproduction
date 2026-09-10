"""Fetch the PromptAnalysis artifacts we audit, pinned to one commit.

PromptAnalysis is the replication package of *related work* cited by the Table
IV paper -- not the Table IV replication package. See docs/data_provenance.md.

Every URL is pinned to a commit SHA; nothing here ever reads a branch. Each
download is recorded in a manifest with its size, SHA256 and timestamp, and a
local file whose checksum disagrees with the manifest aborts the run unless
--force is given.

    python scripts/fetch_related_artifacts.py [--force] [--verify-only]
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "Amal-AK/PromptAnalysis"
COMMIT = "99f5d447aa55167e177069d90f00f81933002e05"
RAW = "https://raw.githubusercontent.com"

ARTIFACTS = [
    "datasets/humanEval/HumanEval_US_mutated.jsonl",
    "datasets/humanEval/HumanEval.jsonl",
    "inference_results/openai/gpt-5-mini__HumanEval.json",
    "inference_results/openai/gpt-5-mini__HumanEval_US_with_tests.json",
]

DEST = Path(__file__).resolve().parents[1] / "data" / "external" / "promptanalysis"
MANIFEST = DEST / "manifest.json"


def url_for(path: str) -> str:
    return f"{RAW}/{REPO}/{COMMIT}/{path}"


def local_for(path: str) -> Path:
    return DEST / Path(path).name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"repo": REPO, "commit": COMMIT, "artifacts": {}}


def download(path: str) -> bytes:
    url = url_for(path)
    request = urllib.request.Request(url, headers={"User-Agent": "table-iv-reproduction"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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

    DEST.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    if manifest.get("commit") != COMMIT and manifest.get("artifacts") and not args.force:
        print(
            f"ERROR: manifest was written for commit {manifest.get('commit')}, "
            f"this script is pinned to {COMMIT}. Re-run with --force to replace.",
            file=sys.stderr,
        )
        return 1
    records = manifest["artifacts"]
    problems: list[str] = []

    for path in ARTIFACTS:
        name = Path(path).name
        target = local_for(path)
        recorded = records.get(name)

        if target.exists():
            digest = sha256(target.read_bytes())
            if recorded and recorded["sha256"] != digest:
                message = (
                    f"{name}: local file checksum {digest[:16]}... differs from the "
                    f"manifest's {recorded['sha256'][:16]}..."
                )
                if not args.force:
                    problems.append(message + " (use --force to overwrite)")
                    print(f"  MISMATCH {message}")
                    continue
                print(f"  FORCED   {message} -- re-downloading")
            elif recorded:
                print(f"  OK       {name} ({recorded['size']} bytes, verified)")
                continue

        if args.verify_only:
            problems.append(f"{name}: absent or unrecorded, and --verify-only was given")
            print(f"  MISSING  {name}")
            continue

        try:
            payload = download(path)
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            problems.append(f"{name}: download failed -- {exc}")
            print(f"  FAILED   {name}: {exc}")
            continue

        digest = sha256(payload)
        if recorded and recorded["sha256"] != digest and not args.force:
            problems.append(
                f"{name}: upstream checksum {digest[:16]}... differs from the manifest "
                f"at the same commit -- refusing to overwrite (use --force)"
            )
            print(f"  CONFLICT {name}: upstream content changed at a pinned commit")
            continue

        target.write_bytes(payload)
        records[name] = {
            "repo_path": path,
            "url": url_for(path),
            "commit": COMMIT,
            "size": len(payload),
            "sha256": digest,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        print(f"  FETCHED  {name} ({len(payload)} bytes, sha256 {digest[:16]}...)")

    manifest["repo"] = REPO
    manifest["commit"] = COMMIT
    manifest["source"] = f"https://github.com/{REPO}/tree/{COMMIT}"
    manifest["note"] = (
        "Replication package of related work cited by the Table IV paper, NOT the "
        "Table IV replication package. See docs/data_provenance.md."
    )
    manifest["artifacts"] = records
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nManifest: {MANIFEST}")
    if problems:
        print("\nProblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"All {len(ARTIFACTS)} artifacts present and verified at commit {COMMIT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
