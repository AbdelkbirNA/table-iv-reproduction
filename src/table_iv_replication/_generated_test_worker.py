"""Child process that runs one generated test against a program, twice.

Generated tests are arbitrary untrusted Python. They are executed **only here**,
never in the research process. Launched as
``python _generated_test_worker.py <plan.pkl> <results.jsonl>``; imports only the
output comparator, loaded by path so no PYTHONPATH is needed.

Per test the worker produces three things:

1. the test's status against the **reference** program,
2. its status against the **faulty** program,
3. every invocation of the entry point either run made, recorded at runtime by a
   proxy rather than parsed out of the source, then replayed against both
   programs to decide whether any input distinguishes them.

One flushed JSON line ``[[test_index], payload]`` per test, so a kill costs at
most the test in flight.

This is process isolation for research artifacts, not an adversarial sandbox: a
test that deliberately escapes (filesystem writes, network, os._exit) is not
contained. See docs/llm_plain_reconstruction.md.
"""

import importlib.util
import itertools
import json
import pickle
import signal
import sys
import types
from copy import deepcopy
from pathlib import Path

PASS = "PASS"
ASSERTION_FAILURE = "ASSERTION_FAILURE"
RUNTIME_ERROR = "RUNTIME_ERROR"
TIMEOUT = "TIMEOUT"
INVALID_TEST = "INVALID_TEST"

REPR_LIMIT = 300
_counter = itertools.count()


class _Timeout(Exception):
    pass


def _short(value, limit=REPR_LIMIT):
    try:
        text = repr(value)
    except BaseException as exc:
        text = f"<unreprable {type(value).__name__}: {exc}>"
    return text if len(text) <= limit else text[:limit] + "..."


class _Recorder:
    """Proxy over the entry point that records every call before delegating."""

    def __init__(self, fn):
        self._fn = fn
        self.calls = []

    def __call__(self, *args, **kwargs):
        order = len(self.calls)
        # Copy before the call: the program may mutate its arguments in place,
        # and the replay needs the inputs as the test passed them.
        record = {
            "order": order,
            "args": deepcopy(args),
            "kwargs": deepcopy(kwargs),
        }
        try:
            result = self._fn(*args, **kwargs)
        except BaseException as exc:
            record["outcome"] = ["exception", type(exc).__name__]
            self.calls.append(record)
            raise
        record["outcome"] = ["return", _short(result)]
        self.calls.append(record)
        return result

    def __getattr__(self, name):  # keep attribute access working
        return getattr(self._fn, name)


def _load_program(path, entry_point):
    name = f"table_iv_program_{next(_counter)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, getattr(module, entry_point)


def _install_aliases(aliases, module, entry_point, recorder):
    """Expose the program under a few plausible import names.

    The generation prompt inlines the program and asks for tests only, so a test
    may call the entry point directly or import it. Any *other* import spelling
    fails loudly as INVALID_TEST rather than being silently patched up.
    """
    installed = []
    for alias in aliases:
        shim = types.ModuleType(alias)
        for attribute in dir(module):
            if not attribute.startswith("__"):
                setattr(shim, attribute, getattr(module, attribute))
        setattr(shim, entry_point, recorder)
        sys.modules[alias] = shim
        installed.append(alias)
    return installed


def _classify(exc):
    if isinstance(exc, AssertionError):
        return ASSERTION_FAILURE
    if isinstance(exc, (ImportError, SyntaxError, IndentationError)):
        return INVALID_TEST
    return RUNTIME_ERROR


def _run_one(program_path, entry_point, test, aliases, timeout):
    """Execute `test` against one program. Returns (status, error, invocations)."""
    try:
        module, fn = _load_program(program_path, entry_point)
    except BaseException as exc:
        return INVALID_TEST, f"program failed to load: {type(exc).__name__}: {exc}", []

    recorder = _Recorder(fn)
    aliases_installed = _install_aliases(aliases, module, entry_point, recorder)
    namespace = {"__name__": "table_iv_generated_test", entry_point: recorder}

    status, error = PASS, None
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        exec(compile(test["module_source"], "<generated_test>", "exec"), namespace)
        if not test["module_level"]:
            function = namespace.get(test["name"])
            if not callable(function):
                status, error = INVALID_TEST, f"{test['name']!r} is not defined by the test module"
            else:
                function()
    except _Timeout:
        status, error = TIMEOUT, f"exceeded {timeout}s"
    except BaseException as exc:
        status, error = _classify(exc), f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for alias in aliases_installed:
            sys.modules.pop(alias, None)

    return status, error, recorder.calls


def _call(fn, args, kwargs, timeout):
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return True, fn(*deepcopy(args), **deepcopy(kwargs)), None
    except _Timeout:
        return False, None, "timeout"
    except BaseException as exc:
        return False, None, f"exception:{type(exc).__name__}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main(plan_path, out_path):
    with open(plan_path, "rb") as handle:
        plan = pickle.load(handle)

    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))

    spec = importlib.util.spec_from_file_location(
        "table_iv_output_oracle", str(Path(__file__).with_name("_output_oracle.py"))
    )
    oracle = importlib.util.module_from_spec(spec)
    sys.modules["table_iv_output_oracle"] = oracle
    spec.loader.exec_module(oracle)
    compare_outputs = oracle.compare_outputs

    entry_point = plan["entry_point"]
    aliases = plan.get("module_aliases", ())
    timeout = plan["timeout"]

    with open(out_path, "a", encoding="utf-8") as out:
        for (index,) in plan["items"]:
            test = plan["tests"][index]

            reference_status, reference_error, reference_calls = _run_one(
                plan["reference_path"], entry_point, test, aliases, timeout
            )
            faulty_status, faulty_error, faulty_calls = _run_one(
                plan["faulty_path"], entry_point, test, aliases, timeout
            )

            # Replay every distinct input either run exercised. A test may call
            # the entry point many times, and the two runs may take different
            # paths, so the union is what matters.
            seen, replay_inputs = set(), []
            for source, calls in (("reference", reference_calls), ("faulty", faulty_calls)):
                for call in calls:
                    key = _short(call["args"], 10_000) + "|" + _short(call["kwargs"], 10_000)
                    if key not in seen:
                        seen.add(key)
                        replay_inputs.append((call["args"], call["kwargs"], source))

            replay, triggered, unobserved = [], False, 0
            if replay_inputs:
                _, reference_fn = _load_program(plan["reference_path"], entry_point)
                _, faulty_fn = _load_program(plan["faulty_path"], entry_point)
                for args, kwargs, source in replay_inputs:
                    ref_ok, ref_value, ref_label = _call(reference_fn, args, kwargs, timeout)
                    flt_ok, flt_value, flt_label = _call(faulty_fn, args, kwargs, timeout)

                    if "timeout" in (ref_label, flt_label):
                        differs, reason = None, f"unobserved: ref={ref_label} faulty={flt_label}"
                        unobserved += 1
                    elif ref_ok and flt_ok:
                        agree, why = compare_outputs(
                            flt_value, ref_value,
                            entry_point=entry_point, args=args,
                            atol=plan["atol"], dataset=plan["dataset"],
                        )
                        differs, reason = (not agree), why
                    elif not ref_ok and not flt_ok:
                        differs = ref_label != flt_label
                        reason = (
                            f"both raised {ref_label}" if not differs
                            else f"different exceptions ({flt_label} vs {ref_label})"
                        )
                    else:
                        differs = True
                        reason = f"one side raised: faulty={flt_label or 'ok'} reference={ref_label or 'ok'}"

                    if differs:
                        triggered = True
                    replay.append({
                        "args": _short(args),
                        "kwargs": _short(kwargs),
                        "first_seen_in": source,
                        "reference_outcome": ref_label or _short(ref_value),
                        "faulty_outcome": flt_label or _short(flt_value),
                        "differs": differs,
                        "reason": reason,
                    })

            def serialize(calls):
                return [
                    {
                        "order": call["order"],
                        "args": _short(call["args"]),
                        "kwargs": _short(call["kwargs"]),
                        "outcome": call["outcome"],
                    }
                    for call in calls
                ]

            payload = {
                "reference": {
                    "status": reference_status,
                    "error": reference_error,
                    "invocations": serialize(reference_calls),
                },
                "faulty": {
                    "status": faulty_status,
                    "error": faulty_error,
                    "invocations": serialize(faulty_calls),
                },
                "replay": replay,
                "triggered": triggered,
                "invocation_count": len(reference_calls) + len(faulty_calls),
                "distinct_inputs": len(replay_inputs),
                "unobserved_inputs": unobserved,
            }
            out.write(json.dumps([[index], payload]) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
