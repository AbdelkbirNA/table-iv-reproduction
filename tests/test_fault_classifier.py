from table_iv_replication.fault_classifier import (
    CORRECT,
    CORRECT_SECONDARY,
    DIFFICULTY_THRESHOLD,
    FAULTY,
    FAULTY_SECONDARY,
    INCONCLUSIVE,
    PROMPT_ORIGINAL,
    PROMPT_US,
    SENS_CORRECT_BOTH,
    SENS_EVALPLUS_ONLY,
    SENS_FAULTY_BOTH,
    SENS_INCONCLUSIVE,
    SENS_ORIGINAL_ONLY,
    UNUSABLE,
    Candidate,
    classify_task,
    summarize,
)

REFERENCE = "def f(x):\n    return x * 2\n"
EQUIVALENT = "def f(x):\n    return x + x\n"
ALWAYS_WRONG = "def f(x):\n    return x * 3\n"
WRONG_ON_ONE = "def f(x):\n    return 99 if x == 2 else x * 2\n"
RAISES = "def f(x):\n    return x * 2 // 0\n"
HANGS = "def f(x):\n    while x == 2:\n        pass\n    return x * 2\n"
NO_ENTRY_POINT = "def g(x):\n    return x * 2\n"

FOUR = [[1], [2], [3], [4]]


def candidate(source, variant=PROMPT_ORIGINAL, task="T/0"):
    return Candidate(f"{task}|{variant}", task, variant, "test-artifact", source)


def classify(sources, inputs=FOUR, *, original_reference=REFERENCE, **kwargs):
    variants = [PROMPT_ORIGINAL, PROMPT_US]
    candidates = [candidate(s, variants[i]) for i, s in enumerate(sources)]
    return classify_task(
        "T/0", "f", inputs, candidates,
        evalplus_reference=REFERENCE,
        original_reference=original_reference,
        **kwargs,
    )


def test_correct_candidate_is_not_faulty():
    (result,) = classify([EQUIVALENT])
    assert result.primary_classification == CORRECT
    assert result.triggered_inputs == 0
    assert result.observed_inputs == 4
    assert result.unobserved_inputs == 0
    assert result.provisional_difficult_candidate is False


def test_faulty_candidate_is_detected_on_every_input():
    (result,) = classify([ALWAYS_WRONG])
    assert result.primary_classification == FAULTY
    assert result.triggered_inputs == 4
    assert result.trigger_ratio == 1.0
    assert result.evalplus_domain_difficulty == 0.0
    assert result.first_triggering_indices == [0, 1, 2, 3]
    assert result.examples[0]["candidate_outcome"] == "3"


def test_candidate_raising_an_exception_is_faulty_not_an_infrastructure_error():
    (result,) = classify([RAISES])
    assert result.primary_classification == FAULTY
    assert result.loadable is True
    assert result.observed_inputs == 4        # exceptions are observations
    assert result.unobserved_inputs == 0
    assert "ZeroDivisionError" in result.examples[0]["candidate_outcome"]


def test_candidate_differing_on_a_single_input():
    (result,) = classify([WRONG_ON_ONE])
    assert result.primary_classification == FAULTY
    assert result.triggered_inputs == 1
    assert result.first_triggering_indices == [1]
    assert result.evalplus_domain_difficulty == 0.75


def test_difficulty_is_the_complement_of_the_trigger_ratio():
    (result,) = classify([WRONG_ON_ONE], [[1], [2], [3], [4], [5]])
    assert result.triggered_inputs == 1
    assert result.trigger_ratio == 1 / 5
    assert result.evalplus_domain_difficulty == 0.8


def test_threshold_is_inclusive_at_exactly_0_75():
    # 1 of 4 inputs triggers -> difficulty exactly 0.75.
    (at_threshold,) = classify([WRONG_ON_ONE], FOUR)
    assert at_threshold.evalplus_domain_difficulty == DIFFICULTY_THRESHOLD
    assert at_threshold.provisional_difficult_candidate is True

    # 2 of 4 -> 0.5, below the threshold.
    (below,) = classify(["def f(x):\n    return 99 if x in (2, 3) else x * 2\n"], FOUR)
    assert below.evalplus_domain_difficulty == 0.5
    assert below.provisional_difficult_candidate is False


def test_a_correct_candidate_is_never_a_difficult_candidate():
    # Difficulty is 1.0 arithmetically, but the flag requires FAULTY_PRIMARY.
    (result,) = classify([EQUIVALENT])
    assert result.evalplus_domain_difficulty == 1.0
    assert result.provisional_difficult_candidate is False


def test_reference_sensitivity_when_the_two_references_disagree():
    # The secondary reference is wrong on input [2]; the candidate matches it.
    (result,) = classify([WRONG_ON_ONE], original_reference=WRONG_ON_ONE)
    assert result.primary_classification == FAULTY
    assert result.secondary_classification == CORRECT_SECONDARY
    assert result.sensitivity == SENS_EVALPLUS_ONLY

    # And the mirror image: candidate matches EvalPlus, differs from the other.
    (mirror,) = classify([EQUIVALENT], original_reference=ALWAYS_WRONG)
    assert mirror.primary_classification == CORRECT
    assert mirror.secondary_classification == FAULTY_SECONDARY
    assert mirror.sensitivity == SENS_ORIGINAL_ONLY


def test_agreeing_references_give_agreeing_labels():
    correct_result, faulty_result = classify([EQUIVALENT, ALWAYS_WRONG])
    assert correct_result.sensitivity == SENS_CORRECT_BOTH
    assert faulty_result.sensitivity == SENS_FAULTY_BOTH


def test_a_hanging_candidate_is_inconclusive_not_correct():
    (result,) = classify([HANGS], timeout=0.3)
    assert result.primary_classification == INCONCLUSIVE
    assert result.triggered_inputs == 0
    assert result.unobserved_inputs == 1      # only input [2] hangs
    assert result.observed_inputs == 3
    assert result.sensitivity == SENS_INCONCLUSIVE


def test_blank_and_unloadable_code_are_unusable_not_correct():
    blank, missing = classify(["", NO_ENTRY_POINT])
    assert blank.primary_classification == UNUSABLE
    assert blank.generated_code_present is False
    assert missing.primary_classification == UNUSABLE
    assert missing.loadable is False
    assert "no attribute" in missing.diagnostics["load_error"]


def test_summary_aggregates_only_over_faulty_candidates():
    results = classify([ALWAYS_WRONG, EQUIVALENT])
    summary = summarize(results)
    assert summary["total_records"] == 2
    assert summary["faulty_primary"] == 1
    assert summary["correct_on_observed_domain"] == 1
    assert summary["difficulty"]["n"] == 1
    assert summary["difficulty"]["mean"] == 0.0
