"""Fetch the public YATE / LLM-Plain files we reconstruct the protocol from.

YATE is the authors' public Java/Kotlin implementation. Its Python
implementation is work in progress (see docs/data_provenance.md section F), so
these files document the *Java* Plain-LLM workflow, which is evidence about --
not proof of -- the Table IV Python configuration.

    python scripts/fetch_yate_artifacts.py [--force] [--verify-only]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from table_iv_replication.artifact_fetch import Source, main  # noqa: E402

YATE = Source(
    name="yate",
    repo="michaelkonstantinou/yate-java",
    commit="82b547717e7298b5c541c6065bf46bbef61727c0",
    paths=(
        "prompts/system.txt",
        "prompts/ablation_generate_simple.txt",
        "prompts/ablation_generate_simple_method_named.txt",
        "prompts/fix_errors.txt",
        "src/main/java/com/mkonst/evaluation/ablation/SimpleUnitTestGenerator.kt",
        "src/main/java/com/mkonst/evaluation/YatePlainRunner.kt",
        "src/main/java/com/mkonst/components/YatePlainErrorFixer.kt",
        "src/main/java/com/mkonst/models/ChatOpenAIModel.kt",
        "src/main/java/com/mkonst/services/PromptService.java",
        ".env.dev",
        # Fetched additionally so the workflow reconstruction rests on source
        # rather than inference: these hold the generate->fix orchestration, the
        # model-response code extraction, and the conversation container.
        "src/main/java/com/mkonst/runners/YateAbstractRunner.kt",
        "src/main/java/com/mkonst/components/YateUnitGenerator.kt",
        "src/main/java/com/mkonst/components/AbstractModelComponent.kt",
        "src/main/java/com/mkonst/types/YateResponse.kt",
        "src/main/java/com/mkonst/types/CodeResponse.kt",
    ),
    note=(
        "Public YATE (Java/Kotlin) implementation of LLM-Plain, pinned. Evidence about "
        "the Plain-LLM workflow; NOT the Table IV Python implementation, which is not "
        "publicly available. See docs/llm_plain_reconstruction.md."
    ),
    flatten=False,
)


if __name__ == "__main__":
    raise SystemExit(main(YATE, description=__doc__))
