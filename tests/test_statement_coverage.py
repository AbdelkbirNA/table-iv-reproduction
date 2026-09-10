from table_iv_replication.statement_coverage import (
    executable_lines,
    load_entry_point,
    measure_all,
    measure_one,
    measure_source,
    union_lines,
    write_target,
)

SOURCE = """def classify(n):
    if n > 0:
        return "pos"
    return "non-pos"
"""


def build(tmp_path):
    target = write_target(SOURCE, tmp_path / "target.py")
    return target, load_entry_point(target, "classify", module_name="t_target")


def test_executable_lines(tmp_path):
    target, _ = build(tmp_path)
    assert executable_lines(target) == frozenset({1, 2, 3, 4})


def test_branches_produce_different_line_sets(tmp_path):
    target, fn = build(tmp_path)
    observations = measure_all(fn, [[1], [-1]], target)
    assert [o.result for o in observations] == ["pos", "non-pos"]
    assert observations[0].lines == frozenset({2, 3})
    assert observations[1].lines == frozenset({2, 4})
    assert union_lines(observations) == frozenset({2, 3, 4})


def test_only_the_target_file_is_measured(tmp_path):
    target, fn = build(tmp_path)
    observation = measure_one(fn, [1], target)
    assert observation.measured_files == frozenset({str(target.resolve())})


def test_runtime_error_is_recorded_not_hidden(tmp_path):
    target, fn = build(tmp_path)
    observation = measure_one(fn, ["not-a-number"], target)
    assert observation.error is not None
    assert "TypeError" in observation.error
    assert observation.lines == frozenset({2})


# --- adequacy is measured against the faulty program under test -------------
# See docs/experiment_plan.md: the canonical implementation only supplies the
# expected behaviour; statement/branch adequacy belongs to the faulty program.

CANONICAL = """def classify(n):
    if n > 0:
        return "pos"
    return "non-pos"
"""

FAULTY = """def classify(n):
    label = "pos"
    if n >= 0:
        return label
    return "non-pos"
"""


def test_statement_coverage_works_against_a_faulty_implementation():
    inputs = [[5], [0], [-5]]
    faulty_obs, faulty_statements = measure_source(FAULTY, "classify", inputs)
    canonical_obs, canonical_statements = measure_source(CANONICAL, "classify", inputs)

    # The faulty program has its own line numbering and its own adequacy items.
    assert faulty_statements == frozenset({1, 2, 3, 4, 5})
    assert canonical_statements == frozenset({1, 2, 3, 4})
    assert [sorted(o.lines) for o in faulty_obs] == [[2, 3, 4], [2, 3, 4], [2, 3, 5]]
    assert [sorted(o.lines) for o in canonical_obs] == [[2, 3], [2, 4], [2, 4]]
    assert union_lines(faulty_obs) != union_lines(canonical_obs)


def test_canonical_output_identifies_the_fault_triggering_input():
    inputs = [[5], [0], [-5]]
    faulty_obs, _ = measure_source(FAULTY, "classify", inputs)
    canonical_obs, _ = measure_source(CANONICAL, "classify", inputs)

    triggering = [
        f.index
        for f, c in zip(faulty_obs, canonical_obs)
        if f.result != c.result
    ]
    assert triggering == [1]  # n == 0 is the only fault-triggering input
