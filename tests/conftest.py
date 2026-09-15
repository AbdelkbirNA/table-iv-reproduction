"""Shared guards for tests that need fetched external artifacts.

`data/external/**` is deliberately not committed -- it is large, and it is
reproducible from `scripts/fetch_related_artifacts.py`, with provenance pinned
by checksum in the manifests that *are* committed.

Tests that read those artifacts must therefore **skip** on a fresh clone, not
fail. A fresh clone running the README's own `pytest -q` should report a clean
suite with a clear "run the fetch script" note, not a wall of red for a
prerequisite the reader has not been told to satisfy yet.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPTANALYSIS = ROOT / "data" / "external" / "promptanalysis"

#: Every artifact named by the committed manifest must be on disk.
_MANIFEST = PROMPTANALYSIS / "manifest.json"


def _promptanalysis_is_fetched() -> bool:
    if not _MANIFEST.is_file():
        return False
    import json

    names = json.loads(_MANIFEST.read_text(encoding="utf-8")).get("artifacts", {})
    return bool(names) and all((PROMPTANALYSIS / name).is_file() for name in names)


requires_promptanalysis = pytest.mark.skipif(
    not _promptanalysis_is_fetched(),
    reason=(
        "PromptAnalysis artifacts not fetched; run "
        "`python scripts/fetch_related_artifacts.py` first"
    ),
)
