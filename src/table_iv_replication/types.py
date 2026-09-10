from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class TestObservation:
    __test__ = False
    """Precomputed observation for one test against one faulty implementation.

    adequacy_items is criterion-specific:
      - statement ids for statement coverage
      - branch ids for branch coverage
      - killed-mutant ids for mutation score
    """

    test_id: str
    adequacy_items: FrozenSet[str]
    triggers_fault: bool
    detects_fault: bool
