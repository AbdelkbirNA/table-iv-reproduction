"""Core logic for the Table IV reproduction study."""

from .metrics import aggregate_rates
from .sampling import greedy_random_sample, run_repetitions
from .types import TestObservation

__all__ = ["TestObservation", "greedy_random_sample", "run_repetitions", "aggregate_rates"]
