"""Loading and instrumenting an arbitrary target program.

The program under test is *any* Python source string: a canonical reference, a
faulty LLM-generated implementation, or a mutant. Nothing here knows about
HumanEval. Callers supply source code, an entry point name, and test inputs.

Adequacy is measured against the program under test (see
docs/experiment_plan.md), so the same machinery must accept arbitrary sources.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import coverage

_module_counter = itertools.count()

#: Cap on stderr captured from a child, so a noisy target cannot exhaust memory.
STDERR_LIMIT = 64 * 1024


@dataclass(frozen=True)
class ExecutionRecord:
    """Raw result of running one test input under one coverage session."""

    index: int
    result: Any = None
    error: str | None = None
    measured_files: frozenset[str] = frozenset()
    lines: frozenset[int] = frozenset()
    arcs: frozenset[tuple[int, int]] = field(default_factory=frozenset)


def write_target(source: str, path: Path) -> Path:
    """Materialize `source` on disk so coverage.py can map execution to a file."""
    path.write_text(source, encoding="utf-8")
    return path


def load_entry_point(
    path: Path,
    entry_point: str,
    module_name: str | None = None,
) -> Callable[..., Any]:
    """Import `path` under a unique module name and return its entry point."""
    if module_name is None:
        module_name = f"table_iv_target_{next(_module_counter)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, entry_point):
        raise AttributeError(f"{path} defines no entry point named {entry_point!r}")
    return getattr(module, entry_point)


@contextmanager
def target_program(
    source: str,
    entry_point: str,
    *,
    filename: str = "target.py",
    module_name: str | None = None,
) -> Iterator[tuple[Path, Callable[..., Any]]]:
    """Write `source` to a temporary file and yield (path, callable)."""
    with tempfile.TemporaryDirectory(prefix="table_iv_") as tmpdir:
        path = write_target(source, Path(tmpdir) / filename)
        yield path, load_entry_point(path, entry_point, module_name)


def parse_target(path: Path):
    """Static parse of the target file (statements, exit counts, possible arcs)."""
    from coverage.parser import PythonParser

    parser = PythonParser(filename=str(Path(path).resolve()))
    parser.parse_source()
    return parser


def run_under_coverage(
    fn: Callable[..., Any],
    args: Sequence[Any],
    path: Path,
    index: int = 0,
    *,
    branch: bool = False,
    restrict_to_target: bool = True,
) -> ExecutionRecord:
    """Run ``fn(*args)`` under a fresh coverage session scoped to `path`.

    Arguments are deep-copied *before* the session starts so stdlib machinery
    never lands in the collected data. Exceptions are recorded on the record,
    never swallowed silently; partial coverage up to the raise point is kept.
    Set ``restrict_to_target=False`` to see everything coverage would record.
    """
    call_args = deepcopy(list(args))
    target = str(Path(path).resolve())
    cov = coverage.Coverage(
        data_file=None,
        config_file=False,
        include=[target] if restrict_to_target else None,
        branch=branch,
    )

    result: Any = None
    error: str | None = None
    cov.start()
    try:
        result = fn(*call_args)
    except BaseException as exc:  # recorded, never swallowed silently
        error = f"{type(exc).__name__}: {exc}"
    finally:
        cov.stop()

    data = cov.get_data()
    measured: set[str] = set()
    lines: set[int] = set()
    arcs: set[tuple[int, int]] = set()
    for measured_file in data.measured_files():
        resolved = str(Path(measured_file).resolve())
        measured.add(resolved)
        if resolved != target:
            continue
        lines.update(data.lines(measured_file) or ())
        if branch:
            arcs.update(data.arcs(measured_file) or ())

    return ExecutionRecord(
        index=index,
        result=result,
        error=error,
        measured_files=frozenset(measured),
        lines=frozenset(lines),
        arcs=frozenset(arcs),
    )


# --------------------------------------------------------------------------
# isolated streamed execution
# --------------------------------------------------------------------------


def run_streamed_worker(
    worker: Path,
    plan: dict[str, Any],
    items: Sequence[Sequence[Any]],
    work: Path,
    *,
    stall_timeout: float,
) -> tuple[dict[tuple, Any], list[tuple[tuple, str]]]:
    """Run `items` in a child process, restarting past anything that wedges it.

    The child is launched as ``python <worker> <plan.pkl> <results.jsonl>``,
    receives ``plan`` (with ``items`` set to whatever is still pending) and must
    write one flushed JSON line ``[key, payload]`` per completed item, where
    ``key`` is the item. A kill therefore costs at most the item in flight; that
    item is recorded as a failure and the run continues on the remainder, so a
    pathological target cannot stall the experiment.

    Returns ``({tuple(key): payload}, [(tuple(key), "timeout"|"crash: ...")])``.
    """
    import json
    import pickle
    import subprocess
    import time

    results: dict[tuple, Any] = {}
    failures: list[tuple[tuple, str]] = []
    pending = [tuple(item) for item in items]
    plan_path, out_path = work / "plan.pkl", work / "results.jsonl"

    def drain() -> None:
        if not out_path.exists():
            return
        with out_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    key, payload = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    break  # torn final line from a killed worker
                results[tuple(key)] = payload

    while pending:
        out_path.write_text("", encoding="utf-8")
        with plan_path.open("wb") as handle:
            pickle.dump({**plan, "items": pending}, handle)

        proc = subprocess.Popen(
            [sys.executable, str(worker), str(plan_path), str(out_path)],
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
        # Bounded read: a target that floods stderr must not exhaust our memory.
        stderr = (
            proc.stderr.read(STDERR_LIMIT).decode("utf-8", "replace") if proc.stderr else ""
        )
        proc.kill()  # nothing more is wanted from it
        proc.wait()

        drain()
        remaining = [item for item in pending if item not in results]
        if not remaining:
            break

        culprit = remaining[0]
        if stalled:
            reason = "timeout"
        else:
            tail = stderr.strip().splitlines()
            reason = f"crash: {tail[-1][:200]}" if tail else "crash"
        failures.append((culprit, reason))
        results[culprit] = None
        pending = remaining[1:]

    return results, failures
