from table_iv_replication.metrics import aggregate_rates
from table_iv_replication.types import TestObservation


def test_aggregate_rates():
    suites = {
        "f1": [TestObservation("t1", frozenset({"x"}), True, False)],
        "f2": [TestObservation("t2", frozenset({"x"}), True, True)],
        "f3": [TestObservation("t3", frozenset({"x"}), False, False)],
        "f4": [TestObservation("t4", frozenset({"x"}), False, False)],
    }
    ftr, fdr = aggregate_rates(suites)
    assert ftr == 0.5
    assert fdr == 0.25
