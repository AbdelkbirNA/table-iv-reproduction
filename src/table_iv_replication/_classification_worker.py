"""Child process that runs references and generated candidates over one task.

Generated code is untrusted: it is imported and executed here, never in the
research process. Launched as
``python _classification_worker.py <plan.pkl> <results.jsonl>``.

One process per task runs every program -- both references and both candidates
-- over that task's whole input domain, so each input is prepared once and the
reference outcomes are computed once instead of per candidate.

Protocol
--------
A first line ``[["__load__"], {name: null | "error text"}]`` reports which
programs could be imported. Then one flushed line ``[[input_index], payload]``
per input, where payload maps each candidate to its verdict against each
reference. A kill therefore costs at most the input in flight.
"""

import importlib.util
import json
import pickle
import signal
import sys
from copy import deepcopy
from pathlib import Path

MAX_EXAMPLES = 5
REPR_LIMIT = 200


class _Timeout(Exception):
    pass


def _short(value, limit=REPR_LIMIT):
    try:
        text = repr(value)
    except BaseException as exc:
        text = f"<unreprable {type(value).__name__}: {exc}>"
    return text if len(text) <= limit else text[:limit] + "..."


def _load_module(path, module_name, timeout):
    """Import a program. Module-level code is untrusted, so bound it too."""
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module, None
    except _Timeout:
        return None, "timeout while importing"
    except BaseException as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _call(fn, args, timeout):
    """Returns (ok, value, label). An exception is an outcome, not a failure."""
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return True, fn(*deepcopy(args)), None
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

    oracle = _load_module(
        Path(__file__).with_name("_output_oracle.py"), "table_iv_output_oracle",
        timeout=30,
    )[0]
    compare_outputs = oracle.compare_outputs

    entry_point = plan["entry_point"]
    inputs, timeout = plan["inputs"], plan["timeout"]
    budget = plan.get("timeout_budget", 5)
    references, candidates = plan["references"], plan["candidates"]

    functions, load_errors = {}, {}
    for name, path in plan["programs"].items():
        module, error = _load_module(path, f"table_iv_prog_{name}", timeout)
        if module is None:
            load_errors[name] = error
        elif not hasattr(module, entry_point):
            load_errors[name] = f"no attribute {entry_point!r}"
        else:
            functions[name] = getattr(module, entry_point)

    live = [name for name in plan["programs"] if name in functions]
    spent = dict.fromkeys(live, 0)
    examples = {}

    with open(out_path, "a", encoding="utf-8") as out:
        if plan.get("emit_load_report", True):
            out.write(json.dumps([["__load__"], load_errors]) + "\n")
            out.flush()

        for (index,) in plan["items"]:
            args = inputs[index]
            outcomes = {}
            for name in live:
                if spent[name] >= budget:
                    outcomes[name] = (False, None, "skipped")
                    continue
                ok, value, label = _call(functions[name], args, timeout)
                spent[name] += label == "timeout"
                outcomes[name] = (ok, value, label)

            payload = {"verdicts": {}, "examples": {}}
            for candidate in candidates:
                if candidate not in functions:
                    continue
                cand_ok, cand_value, cand_label = outcomes[candidate]
                per_reference = {}
                for reference in references:
                    if reference not in functions:
                        per_reference[reference] = "unavailable"
                        continue
                    ref_ok, ref_value, ref_label = outcomes[reference]
                    blocked = {"timeout", "skipped"}
                    if (cand_label in blocked) or (ref_label in blocked):
                        per_reference[reference] = "unobserved"
                        continue

                    if cand_ok and ref_ok:
                        agree, reason = compare_outputs(
                            cand_value, ref_value,
                            entry_point=entry_point, args=args,
                            atol=plan["atol"], dataset=plan["dataset"],
                        )
                        left, right = _short(cand_value), _short(ref_value)
                    elif not cand_ok and not ref_ok:
                        agree = cand_label == ref_label
                        reason = (
                            f"both raised {cand_label}" if agree
                            else f"different exceptions ({cand_label} vs {ref_label})"
                        )
                        left, right = cand_label, ref_label
                    else:
                        agree = False
                        reason = f"one side raised: candidate={cand_label or 'ok'} reference={ref_label or 'ok'}"
                        left = cand_label or _short(cand_value)
                        right = ref_label or _short(ref_value)

                    per_reference[reference] = "agree" if agree else "differ"
                    if not agree:
                        key = f"{candidate}|{reference}"
                        seen = examples.get(key, 0)
                        if seen < MAX_EXAMPLES:
                            examples[key] = seen + 1
                            payload["examples"][key] = {
                                "input_index": index,
                                "input": _short(args),
                                "candidate_outcome": left,
                                "reference_outcome": right,
                                "reason": reason,
                            }
                payload["verdicts"][candidate] = per_reference

            out.write(json.dumps([[index], payload]) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
