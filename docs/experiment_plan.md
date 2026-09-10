# Experiment plan

## Paper protocol to preserve

For each faulty implementation and each adequacy criterion:

1. Start with the full generated test pool for that fault.
2. Randomly consider tests.
3. Keep a test only when it increases the target adequacy criterion.
4. Stop when the sampled suite reaches the same target-criterion value as the full pool.
5. Evaluate whether the sampled suite triggers the real LLM-generated fault (FTR contribution).
6. Evaluate whether the test oracle detects the real fault (FDR contribution).
7. Repeat 100 times.
8. Aggregate mean FTR/FDR by benchmark and LLM fault model.

## Adequacy target -- experimental assumption A1

**Decision: the adequacy criterion is measured against each faulty
implementation `f`, not against the canonical reference.**

Rationale. The paper's protocol opens with *"Given a faulty implementation f and
the pool of generated tests TS_f..."* and then samples tests according to
whether they increase criterion C. The program whose statements, branches and
mutants are being covered is therefore the program under test, i.e. `f`.

Consequences for the pipeline:

- Statement and branch adequacy items are line numbers / branch arcs **of the
  faulty source file**. Two faults for the same task generally have different
  line numbering, so adequacy items are only comparable within one fault.
- The canonical implementation is used **only for behavioural comparison**:
  running it on an input yields the expected output, which decides whether a
  test triggers the fault (`TestObservation.triggers_fault`). It never
  contributes adequacy items.
- The coverage layer (`src/table_iv_replication/target.py`,
  `statement_coverage.py`, `branch_coverage.py`) therefore accepts arbitrary
  target source, an entry point name and test inputs. No HumanEval code is
  hard-coded into it.
- `scripts/smoke_statement_coverage.py` and `scripts/smoke_branch_coverage.py`
  run against the HumanEval/0 canonical solution as **infrastructure validation
  only**; the canonical source merely plays the role of a program under test in
  those smoke checks.

**This is our interpretation, not a documented statement from the paper.** If a
replication package later reveals a different convention (for example adequacy
computed once against the reference solution, or against a shared instrumented
harness), only the call sites change: the sampler in `sampling.py` consumes
opaque `adequacy_items` and is unaffected. Re-run the pipeline with the other
convention and report both if the difference is material.

## Coverage instrumentation decisions

1. **Per-test isolation.** Each test input runs in its own `coverage.Coverage`
   session scoped with `include=[<target file>]`. Arguments are deep-copied
   before the session starts so `copy` and EvalPlus internals never enter the
   data.
2. **Statement items** are the executed line numbers of the target file.
   Module-level lines (imports, `def`) execute at import time, before any
   session, so no individual test can claim them. The full-pool target is the
   union over tests, so this is self-consistent, but the denominator is smaller
   than "all executable statements" -- state it whenever a raw coverage
   percentage is reported.
3. **Branch items** are executed arcs `(origin, destination)` where `origin` is
   a branch point (`PythonParser.exit_counts()[origin] > 1`) *and* the arc is
   one of that point's statically possible outcomes. Sequential arcs and the
   synthetic entry/exit arcs (`-1`) are not adequacy items.
4. **Exception exits are excluded.** An exception escaping a branch point is
   recorded by coverage.py as `(origin, -1)` even when the source has no such
   branch. Counting it would inflate the full-pool target with an outcome no
   correct execution can reach. Such arcs are retained on
   `BranchCoverage.discarded_arcs` for inspection, never as adequacy.
5. **Runtime errors are recorded, not swallowed.** A crashing test keeps the
   coverage it accumulated up to the raise point and carries the exception on
   `.error`. This matters: LLM-generated faults frequently crash rather than
   return a wrong value.

## Mutation adequacy -- experimental assumptions A2 and A3

### A2: mutmut 3.7.0 is a provisional engine, not the authors' tool

The paper defines mutation testing conceptually but **does not identify the
Python mutation engine or the operator set** it used. We do not currently have
that information. `mutmut==3.7.0` is pinned in this project and used as the
mutation engine **as a provisional reproduction choice only**. Nothing in this
repository claims mutmut reproduces the authors' operators.

Consequences and mitigations:

- Mutant counts, and therefore mutation-adequate suite sizes, depend on the
  operator set. A different engine (mutpy, cosmic-ray, a hand-rolled operator
  set) would produce a different number of mutants and plausibly different FTR.
  Any Table IV number produced with this adapter must be reported as
  "mutation adequacy under mutmut 3.7.0", not as the paper's mutation criterion.
- The engine is isolated behind `src/table_iv_replication/mutation_coverage.py`.
  Replacing it means implementing `generate_mutants(source, entry_point) ->
  (list[Mutant], module_source)`; the kill loop, the sampler and the metrics are
  engine-agnostic.
- If the replication package later names another tool or operator set, swap the
  adapter and re-run. Record both results if they differ materially.

### A3: full-pool mutation adequacy, and what a survivor is

The equivalent-mutant problem is undecidable and we do not attempt to solve it.
Following the paper's generic stopping condition -- a sampled suite is adequate
when it reaches the same criterion value as the full pool -- we define:

> **full-pool mutation adequacy** = the set of mutants killed by at least one
> test in the full pool.

Sampling then retains a test when it kills a mutant not already killed, and
stops when the selected tests kill exactly that set. Mutants no test in the pool
kills are excluded from the target, so sampling always terminates.

Such mutants are recorded as **observational survivors**
(`MutationRun.survivors`). An observational survivor is *not* a proven
equivalent mutant: it may simply need an input the pool does not contain. The
test suite demonstrates the difference -- a mutant that survives a three-input
pool is killed once the missing input is added. Never report survivors as
equivalent mutants, and never report `killed / total_mutants` as a mutation
score without stating which pool produced it.

### Adequacy is measured against the program under test

Consistent with assumption A1: mutants are generated from the **program under
test**. In the smoke tests that is HumanEval/0's canonical solution; in the real
fault experiments it will be **the selected faulty implementation**, and mutant
ids are only comparable within one fault. The canonical implementation is used
solely for behavioural comparison.

### Mutation execution model

1. **One mutated module, not one file per mutant.**
   `mutmut.mutation.file_mutation.mutate_file_contents` emits a single module
   holding the original function, every mutant as a mangled function, and a
   dispatcher. One import serves all mutants; the active one is selected per
   call. HumanEval/0 costs 13 078 (mutant, input) executions in ~0.3 s.
2. **mutmut's runtime is not imported.** mutmut's own trampoline imports
   `mutmut.__main__`, which resolves a process-global, CWD-dependent config at
   import time. We replace that single import line with an equivalent inlined
   dispatcher. The mutant *bodies* -- the scientifically relevant part -- are
   mutmut's output verbatim. A guard raises if mutmut's emitted code stops
   matching that shape, so a mutmut upgrade fails loudly instead of silently.
3. **Fidelity check.** Every input is also run through the mutated module with
   no mutant active. Any disagreement with the clean original module is reported
   as `baseline_mismatches` -- i.e. mutmut's rewrite changed behaviour on its
   own. It must be empty for a run to be trusted.
4. **Kill rule on structured outcomes.** `Outcome(kind, value)` where kind is
   `return` / `exception` / `timeout` / `crash`. A mutant is killed when its
   outcome differs from the original's. An exception is an outcome, not an
   error: original `ValueError` vs mutant `ValueError` is **not** a kill. Return
   values are compared by `repr`, which is exact for the int/float/str/list/bool
   returns HumanEval uses but would need revisiting for objects with
   address-bearing or unstable reprs.
5. **Isolation and timeouts.** All target execution happens in a child process
   (`_mutation_worker.py`), never in the research process -- required once the
   targets are LLM-generated. Each call is bounded by `SIGALRM`
   (`timeout`, default 1 s); a timeout is a recorded outcome, hence a kill. The
   worker streams one flushed result line per item, and the parent kills it if
   output stalls, records the in-flight item as a failure, and restarts on the
   remainder. A pathological mutant therefore costs one item, not the run.
   Known limit: `SIGALRM` cannot interrupt a hang inside a C call; the parent's
   stall detector is the backstop. POSIX only.

## Phase 1

HumanEval + GPT-5-mini.

**The HumanEval+ inputs currently exercised by `scripts/smoke_*.py` are
smoke-test data only.** They validate the instrumentation; they are not the
paper's test pool. Table IV uses the LLM-generated test pool for each fault,
which we have not yet obtained or reconstructed. No number produced from the
HumanEval+ input pool may be presented as a reproduction of Table IV.

The paper reports 84 selected non-trivial HumanEval faults for GPT-5-mini.

## Artifacts we still need

- selected faulty GPT-5-mini HumanEval implementations
- LLM-generated test pool associated with each selected fault
- original test assertions/oracles
- per-test statement/branch coverage data (implemented: computed directly from each target source)
- mutation operator/tool details, or a justified substitute

## Public building blocks already identified

- EvalPlus / HumanEval+ for benchmark ground truth and rigorous tests
- SERVAl's LLMEval-Dataset for HumanEval/MBPP prompt variants (candidate source for defective prompts; not yet confirmed as the exact artifact used here)
- coverage.py for statement and branch instrumentation
- mutmut 3.7.0 as the provisional Python mutation engine (assumption A2); used as a mutant *generator*, not as a test runner

## Known ambiguity log

1. The paper does not identify the exact Python mutation tool/operator set -- see **assumption A2** above. mutmut 3.7.0 is a provisional stand-in behind a replaceable adapter.
2. The paper mentions a replication package but does not expose a URL in the PDF/HTML text currently available.
3. LLM-Plain's public repository says Python support is work in progress, so reproducing the exact generated test pool from that repository is not currently possible.
4. ~~We must confirm whether adequacy is computed against each faulty implementation or another program variant~~ -- resolved as **assumption A1** above: adequacy is measured against the faulty implementation. Still an assumption, not a confirmed convention; instrumentation stays outside the sampling core so it can be switched without rewriting the protocol.
