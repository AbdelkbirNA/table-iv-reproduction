"""The paper's Table IV values must never reach a code path.

`configs/humaneval_gpt5mini.yaml` carries the published FTR/FDR figures so a
reader can compare against them. The moment one of those numbers can be loaded
by the pipeline, the pipeline can be tuned to it and the reproduction is
worthless. These tests fail if that ever becomes possible.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("src", "scripts", "tests")
CONFIG = ROOT / "configs" / "humaneval_gpt5mini.yaml"

#: Every FTR/FDR value published in Table IV's HumanEval block.
PUBLISHED_VALUES = (
    "0.393", "0.450", "0.385", "0.111", "0.161", "0.120", "0.137", "0.103",
    "0.088", "0.138", "0.123", "0.105", "0.056", "0.082", "0.070",
)


SELF = Path(__file__).resolve()


def python_sources():
    """Every Python source in the project except this scanner itself.

    This file necessarily quotes the values it forbids, so including it would
    make every check fail on its own text.
    """
    for directory in CODE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts or path.resolve() == SELF:
                continue
            yield path, path.read_text(encoding="utf-8")


def test_config_still_holds_the_reference_values():
    # Guards the guard: if the config were emptied, the tests below would pass
    # vacuously.
    text = CONFIG.read_text(encoding="utf-8")
    for value in ("0.393", "0.450", "0.385"):
        assert value in text


def test_no_module_reads_the_config():
    offenders = [
        str(path.relative_to(ROOT))
        for path, source in python_sources()
        if "humaneval_gpt5mini.yaml" in source or re.search(r"\byaml\b", source)
    ]
    assert offenders == [], f"config or a YAML loader reached code: {offenders}"


@pytest.mark.parametrize("value", PUBLISHED_VALUES)
def test_no_published_value_appears_in_code(value):
    offenders = [
        str(path.relative_to(ROOT))
        for path, source in python_sources()
        if value in source
    ]
    assert offenders == [], f"published Table IV value {value} found in {offenders}"


def test_yaml_is_not_a_declared_dependency():
    # Nothing can load the config if nothing can parse YAML.
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "yaml" not in pyproject.lower()
