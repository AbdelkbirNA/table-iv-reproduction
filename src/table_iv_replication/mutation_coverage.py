"""Per-test mutation adequacy against an arbitrary target program.

Provisional engine: **mutmut 3.7.0** (see docs/experiment_plan.md, assumption
A2). The original study's mutation tool and operator set are not known to us,
so mutmut is a stand-in, isolated behind this adapter.

How mutants are produced
------------------------
``mutmut.mutation.file_mutation.mutate_file_contents`` returns a single module
containing the original function, every mutant as a separate mangled function,
and a trampoline that dispatches per call. That is far cheaper than one file per
mutant: one import serves every mutant, switched by a module-level selector.

mutmut's own trampoline imports ``mutmut.__main__``, which reads a global,
CWD-dependent config at import time. We replace that one import line with an
equivalent inlined dispatcher (``_SHIM``) so the generated mutant bodies -- the
part that matters scientifically -- are mutmut's, verbatim, while the runtime
dependency disappears.

Kill rule
---------
A mutant is killed by input ``t`` when its observable outcome on ``t`` differs
from the original program's. Outcomes are structured (see :class:`Outcome`):
a returned value, an exception *type*, or a timeout. An exception is an outcome,
not an error: original ``ValueError`` vs mutant ``ValueError`` is *not* a kill.
"""

from __future__ import annotations

import ast
import contextlib
import json
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ORIGINAL = ""
TRAMPOLINE = "~trampoline"
WORKER = Path(__file__).with_name("_mutation_worker.py")

_MUTMUT_IMPORT = re.compile(r"^\s*(?:from|import)\s+mutmut\b", re.MULTILINE)

_SHIM = '''
_MUTMUT_ACTIVE = None


def _mutmut_select(name):
    """Select the active mutant by id; None runs the unmutated original."""
    global _MUTMUT_ACTIVE
    _MUTMUT_ACTIVE = name


def _mutmut_mutated(mutants_dict, is_classmethod=False):
    def decorate(func):
        import inspect as _inspect

        if (_inspect.isgeneratorfunction(func) or _inspect.iscoroutinefunction(func)
                or _inspect.isasyncgenfunction(func)):
            raise NotImplementedError(
                "the mutation shim supports plain functions only, not "
                f"generator/async targets ({func.__name__})"
            )

        def trampoline(*args, **kwargs):
            impl = mutants_dict.get(_MUTMUT_ACTIVE) or mutants_dict["_mutmut_orig"]
            if is_classmethod:
                return getattr(args[0], impl.__name__)(*args[1:], **kwargs)
            return impl(*args, **kwargs)

        trampoline.__name__ = func.__name__
        trampoline.__doc__ = func.__doc__
        return trampoline

    return decorate


MutantDict = dict
'''


@dataclass(frozen=True)
class Outcome:
    """Structured observable outcome of one execution."""

    kind: str  # "return" | "exception" | "timeout" | "crash"
    value: str  # repr of the return value, or the exception type name

    @classmethod
    def from_json(cls, payload: Sequence[str]) -> "Outcome":
        return cls(payload[0], payload[1])

    def __str__(self) -> str:
        return f"{self.kind}:{self.value}" if self.value else self.kind


@dataclass(frozen=True)
class Mutant:
    """One generated mutant and where it came from."""

    id: str
    function: str
    source_line: int | None = None
    before: str | None = None
    after: str | None = None

    @property
    def description(self) -> str:
        if self.before is None:
            return f"{self.function}: (whole-body change)"
        return f"{self.function} L{self.source_line}: {self.before.strip()!r} -> {self.after.strip()!r}"


@dataclass(frozen=True)
class MutationTestCoverage:
    """Mutation adequacy observed for one test input."""

    __test__ = False

    index: int
    killed: frozenset[str]
    original: Outcome
    outcomes: dict[str, Outcome] = field(default_factory=dict, repr=False)

    @property
    def adequacy_items(self) -> frozenset[str]:
        """Criterion items in the form consumed by TestObservation."""
        return self.killed


@dataclass(frozen=True)
class MutationRun:
    """Everything measured for one target program against one input pool."""

    mutants: list[Mutant]
    observations: list[MutationTestCoverage]
    killed: frozenset[str]
    survivors: frozenset[str]
    failures: list[tuple[str, int, Outcome]]
    baseline_mismatches: list[int]
    elapsed: float

    @property
    def mutant_ids(self) -> list[str]:
        return [m.id for m in self.mutants]


# --------------------------------------------------------------------------
# mutant generation
# --------------------------------------------------------------------------


def _mangled_name_pattern() -> re.Pattern[str]:
    """Match mutmut's mangled names: x_func__mutmut_N or x<SEP>Class<SEP>meth__mutmut_N."""
    from mutmut.mutation.trampoline_templates import CLASS_NAME_SEPARATOR as sep

    escaped = re.escape(sep)
    return re.compile(
        rf"^x(?:_|{escaped}(?P<cls>.+?){escaped})(?P<name>.+?)__mutmut_(?P<n>\d+)$"
    )


def _describe(mutated_code: str, spans, original_source: str) -> list[Mutant]:
    """Diff each mutant function against the _mutmut_orig copy.

    Must run on mutmut's *unmodified* output: the line spans index that text.
    """
    mangled = _mangled_name_pattern()
    lines = mutated_code.splitlines()
    original_def_lines = {
        node.name: node.lineno
        for node in ast.walk(ast.parse(original_source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def body(name: str) -> list[str]:
        span = spans[name]
        return lines[span.start - 1 : span.end]

    mutants: list[Mutant] = []
    for name, span in spans.items():
        match = mangled.match(name)
        if match is None:
            continue  # the _mutmut_orig copy
        function = match.group("name")
        orig_name = f"{name.rsplit('__mutmut_', 1)[0]}__mutmut_orig"
        before = after = None
        source_line = original_def_lines.get(function)
        if orig_name in spans:
            orig_body, mutant_body = body(orig_name), body(name)
            # A span can start a couple of blank separator lines before the def,
            # so anchor the mapping back to the original source on the def line.
            def_offset = next(
                (i for i, line in enumerate(orig_body)
                 if line.lstrip().startswith(("def ", "async def "))),
                0,
            )
            if len(orig_body) == len(mutant_body):
                for offset, (a, b) in enumerate(zip(orig_body, mutant_body)):
                    # The def line always differs by the mangled name; normalize
                    # it away so a mutated *signature* is still detected.
                    if a.replace(orig_name, "F") == b.replace(name, "F"):
                        continue
                    before, after = a.replace(orig_name, function), b.replace(name, function)
                    if source_line is not None:
                        source_line += offset - def_offset
                    break
        mutants.append(Mutant(name, function, source_line, before, after))
    return mutants


def generate_mutants(source: str, entry_point: str) -> tuple[list[Mutant], str]:
    """Generate every mutant of `source` and the module that hosts them.

    Returns the mutant metadata and a runnable module exposing the same names as
    `source` plus ``_mutmut_select(mutant_id)``. Nothing on disk is touched: the
    caller's source string is never modified.

    `entry_point` is validated, not used to restrict mutation -- helper
    functions in the target are mutated too, as they are part of the program
    under test.
    """
    if entry_point not in {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }:
        raise ValueError(f"source defines no function named {entry_point!r}")

    from mutmut.configuration import Config
    from mutmut.mutation.file_mutation import mutate_file_contents
    from mutmut.mutation.trampoline_templates import trampoline_imports

    # mutmut resolves a process-global config from the CWD. Give it a scratch
    # directory it can resolve unambiguously, then drop the cached config so we
    # never leak this project's settings into a later call (or vice versa).
    work = Path(tempfile.mkdtemp(prefix="table_iv_mutgen_"))
    try:
        (work / "setup.cfg").write_text("[mutmut]\nsource_paths = .\n", encoding="utf-8")
        (work / "target.py").write_text(source, encoding="utf-8")
        Config.reset()
        with contextlib.chdir(work):
            mutated = mutate_file_contents("target.py", source)
    finally:
        Config.reset()
        shutil.rmtree(work, ignore_errors=True)

    mutants = _describe(mutated.code, mutated.line_span_by_function_name, source)

    if trampoline_imports not in mutated.code:
        raise RuntimeError(
            "mutmut's trampoline import line changed shape; the dispatcher shim "
            "in mutation_coverage.py needs updating for this mutmut version"
        )
    module_source = mutated.code.replace(trampoline_imports, _SHIM, 1)
    if _MUTMUT_IMPORT.search(module_source):
        raise RuntimeError(
            "generated mutant module still imports mutmut at runtime; the shim "
            "no longer covers everything mutmut emits"
        )

    known = {mutant.id for mutant in mutants}
    missing = set(mutated.mutant_names) - known
    if missing:
        raise RuntimeError(f"mutant metadata missing for {sorted(missing)}")

    return mutants, module_source


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def _drain(path: Path) -> dict[tuple[str, int], Outcome]:
    results: dict[tuple[str, int], Outcome] = {}
    if not path.exists():
        return results
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                mutant_id, index, outcome = json.loads(line)
            except json.JSONDecodeError:
                break  # torn final line from a killed worker
            results[(mutant_id, index)] = Outcome.from_json(outcome)
    return results


def _execute(
    plan: dict[str, Any],
    items: list[tuple[str, int]],
    work: Path,
    *,
    stall_timeout: float,
) -> tuple[dict[tuple[str, int], Outcome], list[tuple[str, int, Outcome]]]:
    """Run `items` in a child process, restarting past anything that wedges it.

    The child streams one flushed line per completed item, so a kill costs at
    most the item in flight. That item is recorded as a failure outcome and the
    run continues -- a pathological mutant cannot stall the experiment.
    """
    results: dict[tuple[str, int], Outcome] = {}
    failures: list[tuple[str, int, Outcome]] = []
    pending = list(items)
    plan_path, out_path = work / "plan.pkl", work / "results.jsonl"

    while pending:
        out_path.write_text("", encoding="utf-8")
        with plan_path.open("wb") as handle:
            pickle.dump({**plan, "items": pending}, handle)

        proc = subprocess.Popen(
            [sys.executable, str(WORKER), str(plan_path), str(out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        stalled = False
        size, last_progress = -1, time.monotonic()
        while proc.poll() is None:
            time.sleep(0.02)
            current = out_path.stat().st_size if out_path.exists() else 0
            if current != size:
                size, last_progress = current, time.monotonic()
            elif time.monotonic() - last_progress > stall_timeout:
                proc.kill()
                stalled = True
                break
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()

        results.update(_drain(out_path))
        remaining = [item for item in pending if item not in results]
        if not remaining:
            break

        culprit = remaining[0]
        if stalled:
            outcome = Outcome("timeout", "")
        else:
            tail = stderr.strip().splitlines()
            outcome = Outcome("crash", tail[-1][:200] if tail else "")
        results[culprit] = outcome
        failures.append((culprit[0], culprit[1], outcome))
        pending = remaining[1:]

    return results, failures


def measure_mutation_source(
    source: str,
    entry_point: str,
    inputs: Sequence[Sequence[Any]],
    *,
    timeout: float = 1.0,
    stall_timeout: float | None = None,
    check_baseline: bool = True,
) -> MutationRun:
    """Measure which mutants of `source` each input kills.

    Every (mutant, input) pair is evaluated -- a mutant is not dropped once
    killed, because the sampler needs per-test kill sets, not a global score.
    """
    started = time.monotonic()
    mutants, module_source = generate_mutants(source, entry_point)

    work = Path(tempfile.mkdtemp(prefix="table_iv_mutation_"))
    try:
        original_path = work / "original.py"
        mutants_path = work / "mutants.py"
        original_path.write_text(source, encoding="utf-8")
        mutants_path.write_text(module_source, encoding="utf-8")

        plan = {
            "original_path": str(original_path),
            "mutants_path": str(mutants_path),
            "entry_point": entry_point,
            "inputs": list(inputs),
            "timeout": timeout,
        }
        items: list[tuple[str, int]] = [(ORIGINAL, i) for i in range(len(inputs))]
        if check_baseline:
            items += [(TRAMPOLINE, i) for i in range(len(inputs))]
        items += [(m.id, i) for m in mutants for i in range(len(inputs))]

        results, failures = _execute(
            plan, items, work, stall_timeout=stall_timeout or max(5.0, timeout * 3)
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    observations: list[MutationTestCoverage] = []
    baseline_mismatches: list[int] = []
    for index in range(len(inputs)):
        original = results[(ORIGINAL, index)]
        outcomes = {m.id: results[(m.id, index)] for m in mutants}
        observations.append(
            MutationTestCoverage(
                index=index,
                killed=frozenset(mid for mid, o in outcomes.items() if o != original),
                original=original,
                outcomes=outcomes,
            )
        )
        if check_baseline and results[(TRAMPOLINE, index)] != original:
            baseline_mismatches.append(index)

    killed: set[str] = set()
    for observation in observations:
        killed.update(observation.killed)

    return MutationRun(
        mutants=mutants,
        observations=observations,
        killed=frozenset(killed),
        survivors=frozenset(m.id for m in mutants) - killed,
        failures=failures,
        baseline_mismatches=baseline_mismatches,
        elapsed=time.monotonic() - started,
    )


def union_killed(observations: Sequence[MutationTestCoverage]) -> frozenset[str]:
    """Full-pool mutation adequacy: mutants killed by at least one test."""
    killed: set[str] = set()
    for observation in observations:
        killed.update(observation.killed)
    return frozenset(killed)
