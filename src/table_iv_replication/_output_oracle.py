"""EvalPlus's per-input output oracle, mirrored.

Kept free of relative imports so the audit's child process can load it by path
(see _reference_worker.py) without the package being importable. The public
entry point is re-exported from ``reference_oracle``.

:func:`compare_outputs` mirrors the oracle inside ``evalplus.eval.unsafe_execute``
(evalplus 0.3.1) and *reuses* EvalPlus's own ``is_floats`` and ``_poly`` rather
than reimplementing them, so float, sequence and special-oracle handling cannot
drift from the upstream definition.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def compare_outputs(
    out: Any,
    exp: Any,
    *,
    entry_point: str,
    args: Sequence[Any],
    atol: float,
    dataset: str = "humaneval",
) -> tuple[bool, str]:
    """Mirror of evalplus 0.3.1's per-input oracle. Returns (accepted, reason).

    Deviation from upstream, deliberate: EvalPlus reassigns ``atol`` inside its
    input loop, so a float expectation raises the tolerance for every *later*
    input of the same task. Here the 1e-6 floor is applied per comparison. The
    two agree except where a non-float pair is unequal yet numerically close,
    which cannot arise for the HumanEval return types.
    """
    from evalplus.eval import is_floats
    from evalplus.eval._special_oracle import _poly

    if dataset == "humaneval" and entry_point == "find_zero":
        # HumanEval/32: any root of the polynomial is accepted, so the two
        # references may legitimately return different values.
        try:
            return abs(_poly(*args, out)) <= atol, f"special-oracle find_zero (atol={atol})"
        except BaseException as exc:
            return False, f"special-oracle find_zero raised {type(exc).__name__}: {exc}"

    try:
        exact = bool(out == exp)
    except BaseException:
        exact = False
    if exact:
        return True, "exact equality"

    effective = 1e-6 if (atol == 0 and is_floats(exp)) else atol
    if effective == 0:
        return False, "not equal, and atol == 0 (no numeric tolerance applies)"

    import numpy as np

    if type(out) is not type(exp):
        return False, f"type mismatch: {type(out).__name__} vs {type(exp).__name__}"
    if isinstance(exp, (list, tuple)) and len(out) != len(exp):
        return False, f"length mismatch: {len(out)} vs {len(exp)}"
    try:
        if np.allclose(out, exp, rtol=1e-07, atol=effective):
            return True, f"np.allclose(rtol=1e-07, atol={effective})"
        return False, f"not within np.allclose(rtol=1e-07, atol={effective})"
    except BaseException as exc:
        return False, f"np.allclose raised {type(exc).__name__}: {exc}"
