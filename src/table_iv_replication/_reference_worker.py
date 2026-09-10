"""Child process that runs two reference implementations and compares outputs.

Launched as ``python _reference_worker.py <plan.pkl> <results.jsonl>``. Imports
nothing from this package except the comparator, which it loads by path so the
parent never has to arrange a PYTHONPATH.

Writes one flushed JSON line ``[[input_index], payload]`` per input, so a kill
costs at most the input in flight.
"""

import importlib.util
import json
import pickle
import signal
import sys
from copy import deepcopy
from pathlib import Path


class _Timeout(Exception):
    pass


def _load(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _call(fn, args, timeout):
    """Returns (ok, value_or_None, label). An exception/timeout is an outcome."""
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return True, fn(*deepcopy(args)), None
    except _Timeout:
        return False, None, "timeout"
    except BaseException as exc:
        return False, None, f"exception:{type(exc).__name__}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _short(value, limit=300):
    try:
        text = repr(value)
    except BaseException as exc:
        text = f"<unreprable {type(value).__name__}: {exc}>"
    return text if len(text) <= limit else text[:limit] + "..."


def main(plan_path, out_path):
    with open(plan_path, "rb") as handle:
        plan = pickle.load(handle)

    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(_Timeout()))

    oracle = _load(
        Path(__file__).with_name("_output_oracle.py"), "table_iv_output_oracle"
    )
    compare_outputs = oracle.compare_outputs

    left = getattr(_load(plan["left_path"], "table_iv_ref_left"), plan["entry_point"])
    right = getattr(_load(plan["right_path"], "table_iv_ref_right"), plan["entry_point"])
    inputs, timeout = plan["inputs"], plan["timeout"]
    budget = plan.get("timeout_budget", 5)
    spent = 0

    with open(out_path, "a", encoding="utf-8") as out:
        for (index,) in plan["items"]:
            args = inputs[index]
            if spent >= budget:
                # One side is pathologically slow on this domain. Stop burning
                # CPU; the remaining inputs are reported as unobserved, never
                # as agreement.
                out.write(json.dumps([[index], {
                    "agree": None,
                    "kind": "skipped",
                    "reason": f"not executed: task exceeded its budget of {budget} timeouts",
                    "left": None, "right": None, "args": _short(args),
                }]) + "\n")
                out.flush()
                continue
            left_ok, left_value, left_label = _call(left, args, timeout)
            right_ok, right_value, right_label = _call(right, args, timeout)

            timed_out = "timeout" in (left_label or "", right_label or "")
            spent += timed_out

            if left_ok and right_ok:
                agree, reason = compare_outputs(
                    left_value,
                    right_value,
                    entry_point=plan["entry_point"],
                    args=args,
                    atol=plan["atol"],
                    dataset=plan["dataset"],
                )
                kind = "agree" if agree else "value"
                left_repr, right_repr = _short(left_value), _short(right_value)
            elif timed_out:
                # A timeout is a failure to observe, not an observed difference.
                agree = None
                kind = "timeout"
                reason = f"timed out: left={left_label or 'ok'} right={right_label or 'ok'}"
                left_repr = left_label or _short(left_value)
                right_repr = right_label or _short(right_value)
            elif not left_ok and not right_ok:
                # Both raised -> the exception type is the observable outcome.
                agree = left_label == right_label
                kind = "agree" if agree else "exception"
                reason = (
                    f"both raised the same outcome ({left_label})"
                    if agree
                    else f"different exceptions ({left_label} vs {right_label})"
                )
                left_repr, right_repr = left_label, right_label
            else:
                agree = False
                kind = "exception"
                reason = f"one side raised: left={left_label or 'ok'} right={right_label or 'ok'}"
                left_repr = left_label or _short(left_value)
                right_repr = right_label or _short(right_value)

            payload = {
                "agree": agree,
                "kind": kind,
                "reason": reason,
                "left": left_repr,
                "right": right_repr,
                "args": _short(args),
            }
            out.write(json.dumps([[index], payload]) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
