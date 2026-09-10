"""Protocol model for our Python LLM-Plain reconstruction. No API client.

This module deliberately contains **no network code**: no HTTP client, no
`openai`/`anthropic` import, no credential handling. It models the protocol,
builds prompts as strings, and parses responses that some caller obtained
elsewhere. Sending a prompt to a model is a separate, later step.

Read `docs/llm_plain_reconstruction.md` first. Every configuration value carries
a provenance tag, because the public YATE Java implementation and the Table IV
Python implementation are different artifacts, and most Table IV parameters are
simply unknown. `LLMPlainConfig.require()` refuses to hand out an UNKNOWN value
rather than defaulting it silently.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


class Provenance(str, Enum):
    """Where a configuration value comes from. Never blur these."""

    CONFIRMED_PUBLIC_IMPLEMENTATION = "CONFIRMED_PUBLIC_IMPLEMENTATION"
    CONFIRMED_TARGET_PAPER = "CONFIRMED_TARGET_PAPER"
    INFERRED_ADAPTATION = "INFERRED_ADAPTATION"
    UNKNOWN = "UNKNOWN"
    #: The public Java implementation settles this, the unpublished Python
    #: Table IV implementation does not. Both facts must travel together.
    UNKNOWN_TARGET_PAPER_IMPLEMENTATION = "UNKNOWN_TARGET_PAPER_IMPLEMENTATION"


class UnknownParameter(LookupError):
    """Raised when an UNKNOWN Table IV parameter is required as if it were known."""


CONFIG_FIELDS = (
    "model_name",
    "generation_temperature",
    "max_repair_iterations",
    "generation_requests_per_program",
    "test_framework",
    "language",
    "include_program_under_test_in_prompt",
    "uses_coverage_feedback",
    "repairs_assertion_failures",
)


@dataclass(frozen=True)
class LLMPlainConfig:
    """One LLM-Plain configuration, with a provenance tag per field.

    A field may be ``None`` for two different reasons -- not applicable, or not
    known -- so the provenance map is what distinguishes them. Use
    :meth:`require` when a value must be real.
    """

    label: str
    model_name: str | None = None
    generation_temperature: float | None = None
    max_repair_iterations: int | None = None
    generation_requests_per_program: int | None = None
    test_framework: str | None = None
    language: str | None = None
    include_program_under_test_in_prompt: bool | None = None
    uses_coverage_feedback: bool | None = None
    repairs_assertion_failures: bool | None = None
    provenance: dict[str, Provenance] = field(default_factory=dict)
    #: Independently of where our value comes from, does the *Table IV paper*
    #: settle this field? Defaults to UNKNOWN_TARGET_PAPER_IMPLEMENTATION so a
    #: value read from the public Java code can never be mistaken for a paper fact.
    target_paper: dict[str, Provenance] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = {name: Provenance.UNKNOWN for name in CONFIG_FIELDS}
        unknown.update(self.provenance)
        object.__setattr__(self, "provenance", unknown)
        paper = {
            name: Provenance.UNKNOWN_TARGET_PAPER_IMPLEMENTATION for name in CONFIG_FIELDS
        }
        paper.update(self.target_paper)
        object.__setattr__(self, "target_paper", paper)

    def provenance_of(self, name: str) -> Provenance:
        if name not in CONFIG_FIELDS:
            raise KeyError(f"{name!r} is not a configuration field")
        return self.provenance[name]

    def is_known(self, name: str) -> bool:
        return self.provenance_of(name) is not Provenance.UNKNOWN

    def target_paper_status(self, name: str) -> Provenance:
        """Whether the Table IV paper itself settles this field."""
        if name not in CONFIG_FIELDS:
            raise KeyError(f"{name!r} is not a configuration field")
        return self.target_paper[name]

    def confirmed_by_target_paper(self, name: str) -> bool:
        return self.target_paper_status(name) is Provenance.CONFIRMED_TARGET_PAPER

    def require(self, name: str) -> Any:
        """Return a value, refusing to substitute a default for an unknown one."""
        if not self.is_known(name):
            note = self.notes.get(name, "")
            raise UnknownParameter(
                f"{self.label}: {name} is UNKNOWN and must not be defaulted"
                + (f" -- {note}" if note else "")
            )
        return getattr(self, name)

    def unknown_fields(self) -> list[str]:
        return [name for name in CONFIG_FIELDS if not self.is_known(name)]

    def with_values(self, **values: Any) -> "LLMPlainConfig":
        """Derive a config, tagging every overridden field as an adaptation.

        The target-paper status is untouched: changing our value cannot make the
        paper say something it does not say.
        """
        provenance = dict(self.provenance)
        provenance.update({name: Provenance.INFERRED_ADAPTATION for name in values})
        return replace(self, provenance=provenance, **values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "values": {name: getattr(self, name) for name in CONFIG_FIELDS},
            "provenance": {name: self.provenance[name].value for name in CONFIG_FIELDS},
            "target_paper": {name: self.target_paper[name].value for name in CONFIG_FIELDS},
            "notes": dict(self.notes),
        }


PUBLIC = Provenance.CONFIRMED_PUBLIC_IMPLEMENTATION
PAPER = Provenance.CONFIRMED_TARGET_PAPER
INFERRED = Provenance.INFERRED_ADAPTATION
UNKNOWN = Provenance.UNKNOWN

#: What the pinned public YATE Java/Kotlin code does. Read from source.
PUBLIC_JAVA_PLAIN = LLMPlainConfig(
    label="public YATE Java Plain-LLM (commit 82b5477)",
    model_name=None,
    generation_temperature=0.1,
    max_repair_iterations=5,
    generation_requests_per_program=1,
    test_framework="Junit5",
    language="Java",
    include_program_under_test_in_prompt=True,
    uses_coverage_feedback=False,
    repairs_assertion_failures=True,
    provenance={
        "generation_temperature": PUBLIC,
        "max_repair_iterations": PUBLIC,
        "generation_requests_per_program": PUBLIC,
        "test_framework": PUBLIC,
        "language": PUBLIC,
        "include_program_under_test_in_prompt": PUBLIC,
        "uses_coverage_feedback": PUBLIC,
        "repairs_assertion_failures": PUBLIC,
    },
    notes={
        "model_name": (
            "passed in by the caller; .env.dev has GPT_MODEL=gpt-4.1 but no API_MODEL, "
            "so the null-model branch cannot run from the template"
        ),
        "generation_temperature": "hard-coded in ChatOpenAIModel.executeRequest",
        "max_repair_iterations": "YatePlainRunner constructor default",
        "repairs_assertion_failures": "same loop as compile errors; see section J",
    },
)

#: What the Table IV paper actually pins down. Almost nothing.
TABLE_IV_AS_PUBLISHED = LLMPlainConfig(
    label="Table IV paper, as published",
    language="Python",
    uses_coverage_feedback=False,
    provenance={
        "language": PAPER,
        "uses_coverage_feedback": INFERRED,
    },
    notes={
        "model_name": "the paper does not state which model generated the tests",
        "generation_temperature": (
            "not stated; the 0.8 in the paper is for code generation, not test generation"
        ),
        "max_repair_iterations": "not stated",
        "generation_requests_per_program": "not stated",
        "test_framework": "not stated",
        "include_program_under_test_in_prompt": "not stated",
        "uses_coverage_feedback": (
            "inferred from 'Plain': the public implementation has no coverage phase"
        ),
        "repairs_assertion_failures": (
            "not stated, and consequential: repairing failing assertions on a faulty "
            "program would suppress exactly what FDR measures"
        ),
    },
)

#: Our Python reconstruction, faithful to the public Java Plain workflow.
#:
#: Repair policy corrected 2026-09-11: the public implementation collects errors
#: with ``includeCompilingTests = true`` and repairs whatever the test runner
#: surfaces, assertion failures included, up to 5 times. We follow it, and mark
#: the field CONFIRMED_PUBLIC_IMPLEMENTATION / UNKNOWN_TARGET_PAPER_IMPLEMENTATION
#: rather than pretending the unpublished Python implementation did the same.
PUBLIC_YATE_FAITHFUL = LLMPlainConfig(
    label="Python reconstruction, public-YATE-faithful (RECONSTRUCTION, not reproduction)",
    model_name=None,
    generation_temperature=0.1,
    max_repair_iterations=5,
    generation_requests_per_program=1,
    test_framework="plain pytest-style assert functions (no framework imports)",
    language="Python",
    include_program_under_test_in_prompt=True,
    uses_coverage_feedback=False,
    repairs_assertion_failures=True,
    provenance={
        "generation_temperature": PUBLIC,
        "max_repair_iterations": PUBLIC,
        "generation_requests_per_program": PUBLIC,
        "test_framework": INFERRED,
        "language": PAPER,
        "include_program_under_test_in_prompt": PUBLIC,
        "uses_coverage_feedback": PUBLIC,
        "repairs_assertion_failures": PUBLIC,
    },
    target_paper={"language": PAPER},
    notes={
        "model_name": "must be chosen and recorded explicitly before any generation run",
        "generation_temperature": "0.1, hard-coded in ChatOpenAIModel; paper is silent",
        "max_repair_iterations": "YatePlainRunner default 5; paper is silent",
        "test_framework": (
            "bare `def test_*(): assert ...` functions: collectible by pytest, and also "
            "callable directly by our isolated worker without a framework dependency"
        ),
        "repairs_assertion_failures": (
            "TRUE, matching the public implementation: fixErrors collects errors with "
            "includeCompilingTests=true and feeds them to fix_errors, so a failing "
            "assertion is repaired like a compile error. Consequence to keep in view: "
            "on a faulty program this can bias the oracle toward the faulty behaviour, "
            "which is the very effect the paper's RQ3 investigates. Whether the "
            "unpublished Python implementation did this is UNKNOWN."
        ),
    },
)

#: Sensitivity arm: repair only construction/runtime errors, never assertions.
#: Our choice, not the public behaviour. Run alongside PUBLIC_YATE_FAITHFUL to
#: measure how much the repair policy moves FDR.
ALTERNATIVE_SENSITIVITY_CONFIG = replace(
    PUBLIC_YATE_FAITHFUL,
    label="Python reconstruction, alternative sensitivity arm (assertion repair OFF)",
    repairs_assertion_failures=False,
    provenance={
        **PUBLIC_YATE_FAITHFUL.provenance,
        "repairs_assertion_failures": INFERRED,
    },
    target_paper=dict(PUBLIC_YATE_FAITHFUL.target_paper),
    notes={
        **PUBLIC_YATE_FAITHFUL.notes,
        "repairs_assertion_failures": (
            "FALSE by our choice, deviating from the public implementation on purpose: "
            "on a faulty program a correct assertion fails, and repairing it would "
            "destroy the oracle FDR measures. This arm exists to quantify that effect, "
            "not to claim the paper did it."
        ),
    },
)

# --------------------------------------------------------------------------
# prompts -- semantic adaptation of the public Plain prompts
# --------------------------------------------------------------------------

PYTHON_SYSTEM_PROMPT = "You are a tool used by Python Developers to generate tests."

_GENERATION_TEMPLATE = (
    "The following module is missing unit tests. Please generate all tests needed "
    "to achieve 100% code coverage using {language} and {test_framework}. "
    "Return only the code\n\n{program}"
)

_REPAIR_TEMPLATE = (
    "I receive errors when I run the tests. This is my current implementation.\n\n"
    "{tests}\n\n"
    "Below, I share the errors I get. Please review the errors below and fix the "
    "test module accordingly. Respond only with the fixed code.\n\n"
    "{errors}\n"
)


def build_python_system_prompt(config: LLMPlainConfig = PUBLIC_YATE_FAITHFUL) -> str:
    """Adaptation of prompts/system.txt with %%LANG%% = the config's language."""
    language = config.language or "Python"
    return f"You are a tool used by {language} Developers to generate tests."


def build_python_generation_prompt(
    program_source: str,
    config: LLMPlainConfig = PUBLIC_YATE_FAITHFUL,
) -> str:
    """Adaptation of prompts/ablation_generate_simple.txt.

    Kept minimal on purpose: one message, the program under test inlined, ask for
    tests reaching full coverage, ask for code only. No worked examples, no
    chain-of-thought, no coverage report -- Plain-LLM does none of that, and
    adding any of it would stop being the method under study.
    """
    return _GENERATION_TEMPLATE.format(
        language=config.require("language"),
        test_framework=config.require("test_framework"),
        program=program_source,
    )


def build_python_repair_prompt(
    current_tests: str,
    errors: str,
    config: LLMPlainConfig = PUBLIC_YATE_FAITHFUL,
) -> str:
    """Adaptation of prompts/fix_errors.txt.

    As in the Java implementation, the code echoed back is the *test* module, not
    the program under test, and `errors` is the raw execution log.
    """
    return _REPAIR_TEMPLATE.format(tests=current_tests, errors=errors)


# --------------------------------------------------------------------------
# response parsing -- never executes the parsed code
# --------------------------------------------------------------------------

_FENCE = re.compile(
    r"```[ \t]*([A-Za-z0-9_+#.-]*)[ \t]*\r?\n(.*?)(?:```|\Z)", re.DOTALL
)
_PYTHON_TAGS = {"python", "python3", "py"}


@dataclass(frozen=True)
class CodeBlock:
    language: str
    code: str


def extract_code_blocks(response: str) -> list[CodeBlock]:
    """Every fenced block in the response, in order. Purely textual."""
    return [
        CodeBlock(language=match.group(1).strip().lower(), code=match.group(2))
        for match in _FENCE.finditer(response)
    ]


def extract_test_code(response: str) -> str | None:
    """Pick the test code out of a model response. Never executes it.

    Mirrors ``CodeResponse.extractCodeFromResponse``: prefer the *first* fenced
    block, fall back to the whole response. Two deliberate improvements over the
    Java version, both recorded in docs/llm_plain_reconstruction.md section K:

    * a bare ``` fence is recognised, where the Java regex requires ```lang and
      otherwise returns the surrounding prose as "code";
    * an unterminated final fence is still extracted.
    """
    if not response or not response.strip():
        return None

    blocks = extract_code_blocks(response)
    if blocks:
        for wanted in (_PYTHON_TAGS, {""}, None):
            for block in blocks:
                if wanted is None or block.language in wanted:
                    code = block.code.strip()
                    if code:
                        return code
        return None
    return response.strip() or None


@dataclass(frozen=True)
class TestCaseRecord:
    """One generated test function, kept separable for per-test adequacy."""

    __test__ = False

    name: str
    source: str
    preamble: str = ""
    start_line: int = 0
    end_line: int = 0
    #: True when the "test" is one or more top-level statements rather than a
    #: function. Executing the module *is* running it; there is nothing to call.
    module_level: bool = False

    @property
    def runnable_module(self) -> str:
        """Preamble plus this single test, as a standalone module source.

        The source is the verbatim segment from the extracted code -- never
        re-generated -- so a module-level check keeps its original text.
        """
        parts = [part for part in (self.preamble.strip(), self.source.strip()) if part]
        return "\n\n\n".join(parts) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "preamble": self.preamble,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "module_level": self.module_level,
        }


MODULE_LEVEL_TEST_NAME = "test__module_level"


def split_test_cases(code: str, prefix: str = "test") -> tuple[list[TestCaseRecord], str | None]:
    """Split a test module into individually runnable checks using `ast`.

    Returns ``(records, syntax_error)``. Nothing is executed: only parsed.

    Three kinds of top-level statement are treated differently:

    * a ``prefix*`` function -> one :class:`TestCaseRecord`;
    * a top-level ``assert`` or a bare expression (e.g. a direct call) ->
      collected, in order, into a single ``module_level`` record. An LLM asked to
      "return only the code" often emits checks this way, and dropping them --
      or leaving them in the preamble, where they would re-run for every other
      test -- would silently lose or duplicate assertions;
    * anything else (imports, helper functions, classes, assignments) -> the
      shared preamble, prepended to each record.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [], f"{type(exc).__name__}: {exc}"

    lines = code.splitlines()

    def segment(node: ast.AST) -> tuple[str, int, int]:
        start = min(
            [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        )
        end = node.end_lineno or start
        return "\n".join(lines[start - 1 : end]), start, end

    tests: list[TestCaseRecord] = []
    preamble_parts: list[str] = []
    module_checks: list[tuple[str, int, int]] = []

    for node in tree.body:
        text, start, end = segment(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(prefix):
            tests.append(
                TestCaseRecord(name=node.name, source=text, start_line=start, end_line=end)
            )
        elif isinstance(node, ast.Assert) or (
            isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant)
        ):
            module_checks.append((text, start, end))
        else:
            preamble_parts.append(text)

    if module_checks:
        tests.append(
            TestCaseRecord(
                name=MODULE_LEVEL_TEST_NAME,
                source="\n".join(text for text, _, _ in module_checks),
                start_line=module_checks[0][1],
                end_line=module_checks[-1][2],
                module_level=True,
            )
        )

    preamble = "\n\n".join(preamble_parts)
    return [replace(test, preamble=preamble) for test in tests], None


# --------------------------------------------------------------------------
# conversation and suite records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class LLMPlainConversation:
    """The message history, appended exactly as the Java implementation does.

    The system message is added once at the start and never re-appended; each
    turn appends the user prompt and, if the reply carried extractable code, the
    assistant reply.
    """

    messages: tuple[Message, ...] = ()

    @classmethod
    def start(cls, system_prompt: str) -> "LLMPlainConversation":
        return cls((Message("system", system_prompt),))

    def ask(self, prompt: str) -> "LLMPlainConversation":
        return LLMPlainConversation(self.messages + (Message("user", prompt),))

    def answer(self, reply: str, *, keep: bool = True) -> "LLMPlainConversation":
        """Append the assistant reply. `keep=False` mirrors the Java behaviour of
        discarding a reply that contained no extractable code."""
        if not keep:
            return self
        return LLMPlainConversation(self.messages + (Message("assistant", reply),))

    def to_dict(self) -> dict[str, Any]:
        return {"messages": [message.to_dict() for message in self.messages]}


@dataclass(frozen=True)
class RepairIteration:
    """One repair turn: the errors sent, the reply, and what came back."""

    index: int
    errors: str
    raw_response: str
    extracted_code: str | None
    accepted: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "errors": self.errors,
            "raw_response": self.raw_response,
            "extracted_code": self.extracted_code,
            "accepted": self.accepted,
            "note": self.note,
        }


@dataclass(frozen=True)
class GeneratedTestSuite:
    """Everything one generation produced, raw response included.

    `raw_response` is retained verbatim for reproducibility: the extracted code,
    the split test cases and any repair turn can all be re-derived from it.
    """

    suite_id: str
    task_id: str
    fault_id: str
    config: LLMPlainConfig
    system_prompt: str
    generation_prompt: str
    raw_response: str
    extracted_code: str | None = None
    test_cases: tuple[TestCaseRecord, ...] = ()
    syntax_error: str | None = None
    repairs: tuple[RepairIteration, ...] = ()
    conversation: LLMPlainConversation = LLMPlainConversation()
    provenance_label: str = "RECONSTRUCTION -- not the paper's LLM-Plain pool"

    @classmethod
    def from_response(
        cls,
        suite_id: str,
        task_id: str,
        fault_id: str,
        config: LLMPlainConfig,
        system_prompt: str,
        generation_prompt: str,
        raw_response: str,
        conversation: LLMPlainConversation | None = None,
    ) -> "GeneratedTestSuite":
        code = extract_test_code(raw_response)
        cases, syntax_error = split_test_cases(code) if code else ([], None)
        return cls(
            suite_id=suite_id,
            task_id=task_id,
            fault_id=fault_id,
            config=config,
            system_prompt=system_prompt,
            generation_prompt=generation_prompt,
            raw_response=raw_response,
            extracted_code=code,
            test_cases=tuple(cases),
            syntax_error=syntax_error,
            conversation=conversation or LLMPlainConversation(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic, JSON-serializable form. Same inputs -> same output."""
        return {
            "suite_id": self.suite_id,
            "task_id": self.task_id,
            "fault_id": self.fault_id,
            "provenance_label": self.provenance_label,
            "config": self.config.to_dict(),
            "system_prompt": self.system_prompt,
            "generation_prompt": self.generation_prompt,
            "raw_response": self.raw_response,
            "extracted_code": self.extracted_code,
            "syntax_error": self.syntax_error,
            "test_cases": [case.to_dict() for case in self.test_cases],
            "repairs": [repair.to_dict() for repair in self.repairs],
            "conversation": self.conversation.to_dict(),
        }
