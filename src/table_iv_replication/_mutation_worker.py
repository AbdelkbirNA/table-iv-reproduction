"""Child process that executes (mutant, input) pairs in isolation.

Deliberately self-contained: it is launched as ``python _mutation_worker.py
<plan.pkl> <results.jsonl>`` and imports nothing from this package, so the
parent never has to arrange a PYTHONPATH and no target code is ever imported
into the research process.

Protocol
--------
The plan pickle carries the target sources, the entry point, the test inputs and
a list of work items ``(mutant_id, input_index)``; ``mutant_id`` is "" for the
clean original module and "~trampoline" for the mutated module with no mutant
selected. One JSON line is written and flushed per completed item, so if the
parent has to kill this process the partial results survive and the item it died
on is identifiable by absence.
"""

import importlib.util
import json
import pickle
import signal
import sys
from copy import deepcopy

ORIGINAL = ""
TRAMPOLINE = "~trampoline"


class _Timeout(Exception):
    pass


def _load(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _outcome(fn, args, timeout):
    """Structured outcome: return value, exception type, or timeout."""
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return ["return", repr(fn(*deepcopy(args)))]
    except _Timeout:
        return ["timeout", ""]
    except BaseException as exc:  # an exception IS an observable outcome
        return ["exception", type(exc).__name__]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main(plan_path, out_path):
    with open(plan_path, "rb") as handle:
        plan = pickle.load(handle)

    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))

    original = _load(plan["original_path"], "table_iv_original")
    mutants = _load(plan["mutants_path"], "table_iv_mutants")
    entry_point = plan["entry_point"]
    original_fn = getattr(original, entry_point)
    mutants_fn = getattr(mutants, entry_point)
    inputs = plan["inputs"]
    timeout = plan["timeout"]

    with open(out_path, "a", encoding="utf-8") as out:
        for mutant_id, input_index in plan["items"]:
            if mutant_id == ORIGINAL:
                fn = original_fn
            else:
                fn = mutants_fn
                mutants._mutmut_select(None if mutant_id == TRAMPOLINE else mutant_id)
            outcome = _outcome(fn, inputs[input_index], timeout)
            out.write(json.dumps([mutant_id, input_index, outcome]) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
