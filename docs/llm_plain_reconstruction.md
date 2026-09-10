# LLM-Plain: what the public code actually does

Reconstruction of the **public YATE (Java/Kotlin)** Plain-LLM workflow, pinned to

- repo: <https://github.com/michaelkonstantinou/yate-java>
- commit: `82b547717e7298b5c541c6065bf46bbef61727c0`
- fetched by `scripts/fetch_yate_artifacts.py`, checksums in
  `data/external/yate/manifest.json`

> **This is not the Table IV implementation.** The Table IV paper uses a *Python*
> LLM-Plain; <https://github.com/michaelkonstantinou/llm-plain> states the YATE
> implementation is Java-oriented and the Python implementation is work in
> progress. Everything below is evidence about the Java code. Where the paper is
> silent, the answer is UNKNOWN, never the Java value.

Provenance tags used throughout:

| Tag | Meaning |
|---|---|
| `CONFIRMED_PUBLIC_IMPLEMENTATION` | Read directly from the pinned Java/Kotlin source |
| `CONFIRMED_TARGET_PAPER` | Stated by the Table IV paper |
| `INFERRED_ADAPTATION` | Our own choice for the Python reconstruction |
| `UNKNOWN` | Neither source answers it; not to be guessed |

## A. Generation system prompt — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`prompts/system.txt`, verbatim (61 bytes, single line, no trailing newline):

```
You are a tool used by %%LANG%% Developers to generate tests.
```

`PromptService.replaceCommonVariables` substitutes `%%LANG%%` and
`%%TEST_FRAMEWORK%%` from `.env` at load time. With `.env.dev`
(`LANG=Java`, `TEST_FRAMEWORK=Junit5`) it renders as:

```
You are a tool used by Java Developers to generate tests.
```

## B. Class-level generation prompt — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`prompts/ablation_generate_simple.txt`, verbatim:

```
The following class is missing unit tests. Please generate all tests needed to achieve 100% code coverage using %%LANG%% and %%TEST_FRAMEWORK%%. Return only the code

%%CLASS_CONTENT%%
```

Rendered with `.env.dev`, one user message:

```
The following class is missing unit tests. Please generate all tests needed to achieve 100% code coverage using Java and Junit5. Return only the code

<complete source of the class under test>
```

`SimpleUnitTestGenerator.generateForClass` builds `generationPrompts` with
**exactly one** element and calls `generateTestUsingModel(systemPrompt,
generationPrompts, ...)`. `%%CLASS_CONTENT%%` is
`cutContainer.getCompleteContent()` — **the program under test is included in
the prompt**.

This is what makes Plain *plain*. The full YATE generator
(`YateUnitGenerator.generateForClass`) instead sends a four-message chain:
`identify_tests`, then optionally `specify_private_and_protected_methods`, then
`identify_branch_coverage`, then `generate_tests`. `SimpleUnitTestGenerator`
overrides that with the single prompt above.

## C. Method-level generation prompt — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`prompts/ablation_generate_simple_method_named.txt`, verbatim:

```
The following class is missing unit tests for method %%METHOD_NAME%%. Please generate all tests needed to achieve 100% code coverage for method %%METHOD_NAME%%, using %%LANG%% and %%TEST_FRAMEWORK%%.
The name of the generated test class must be %%CLASS_NAME%%. Return only the code

%%CLASS_CONTENT%%
```

`generateForMethod` sets `CLASS_NAME = "<Class>_<method>_Test"`. Again one
message, again the whole class content.

## D. Error-repair prompt — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`prompts/fix_errors.txt`, verbatim:

```
I receive errors when I run the tests. This is my current implementation.

%%CLASS_CONTENT%%

Below, I share the errors I get. Please review the errors below and fix the test class accordingly. Respond only with the fixed code.

%%ERRORS%%
```

Here `%%CLASS_CONTENT%%` is the **current generated test class**
(`response.testClassContainer.getCompleteContent()`), not the program under
test, and `%%ERRORS%%` is the raw error log from
`YateJavaExecution.runTestsForErrors(..., includeCompilingTests = true)`.

## E. Conversation history — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`ChatOpenAIModel.ask(prompts, systemPrompt, history)`:

- if `history` is non-null it *is* the conversation, and the system prompt is
  **not** re-appended;
- otherwise the conversation starts with the system message;
- each prompt is appended as a user message, the reply as an assistant message,
  and the next prompt sees all of it.

`YatePlainErrorFixer.generateNewTestClass` passes
`history = response.conversation`, so **every repair turn carries the full prior
conversation** — original generation prompt, the class under test, every earlier
attempt and error log. The history is updated **only if the reply contained
extractable code** (`if (modelResponse.codeContent !== null)`).

## F. Maximum repair iterations — `CONFIRMED_PUBLIC_IMPLEMENTATION`: 5

`YatePlainRunner`'s constructor: `private val maxFixIterations: Int = 5`. The
loop:

```kotlin
while (i < maxFixIterations && hasErrors) {
    hasErrors = this.simpleFixer.fixErrors(response)
    i += 1
}
```

`fixErrors` returns `false` as soon as `runTestsForErrors` reports no errors, so
the loop stops early on success. Up to **5** repair requests. `.env.dev` also
carries `MAX_FIX_ITERATIONS=5`, consistent, though `YatePlainRunner` uses its own
constructor default rather than reading that variable.

## G. Temperature — `CONFIRMED_PUBLIC_IMPLEMENTATION`: 0.1

`ChatOpenAIModel.executeRequest`, hard-coded, not configurable:

```kotlin
val chatCompletionRequest = ChatCompletionRequest(
    model = ModelId(this.model),
    temperature = 0.1,
    messages = conversation
)
```

Applies to generation *and* repair, since both go through the same model object.

## H. Model selection — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`ModelProvider.get(modelName)` via `AbstractModelComponent`. In
`ChatOpenAIModel`:

- `modelName == null` → read `API_MODEL`, `API_KEY`, `API_BASE_URL` from `.env`;
- otherwise the string routes the client: contains `deepseek` → DeepSeek
  endpoint; contains `mistral` or `codestral` → Mistral; else the OpenAI client
  with `GPT_API_KEY` / `GPT_ORGANIZATION`.

Observation worth recording: `.env.dev` defines `GPT_MODEL=gpt-4.1` but **no**
`API_MODEL`, `API_KEY` or `API_BASE_URL`, so the `modelName == null` branch
cannot work with the committed template. In practice the model name is always
passed in explicitly. `gpt-4.1` is the template's GPT default — that is a
property of `.env.dev`, **not** evidence about the Table IV test-generation
model.

API-level retry is separate from repair: `MAX_REPEAT_FAILED_API_ITERATIONS=3`
retries a *failed HTTP request*, and does not re-prompt.

## I. Coverage feedback — `CONFIRMED_PUBLIC_IMPLEMENTATION`: none

`YatePlainRunner.enhanceCoverageForClass` returns `null` unconditionally. In
`YateAbstractRunner.enhanceCoverage` a `null` response means nothing is added,
so Plain-LLM performs **no coverage-enhancement phase** and never measures
coverage. The prompt only *asks* for 100% coverage in prose; no coverage tool
result is ever fed back.

By contrast the full YATE `HYBRID` level calls
`CoverageService.getNotFullyCoveredMethodsForClass` and generates extra tests per
uncovered method. Plain does not reach that path.

## J. Are assertion/oracle failures repaired too? — `CONFIRMED_PUBLIC_IMPLEMENTATION`: yes, in the same loop

`YatePlainRunner.fixOraclesInTestClass` returns the response unchanged, with the
comment:

> The YatePlainRunner does not differentiate between compilation fixing and
> oracle fixing. It handles both cases in the fixGeneratedTestClass method

`YatePlainErrorFixer`'s own docstring: *"asks the LLM X times to fix the errors
from failing tests (compile or oracle errors)"*, and it collects errors with
`includeCompilingTests = true`. So a **failing assertion is repaired exactly like
a compile error**, by sending the error log back.

This matters for our FDR work: under this workflow, an LLM assertion that
*correctly* fails on a faulty program is indistinguishable from a broken
assertion, and the loop will try to make it pass. Whether the Table IV pipeline
did the same against faulty implementations is **UNKNOWN** and consequential.

## K. Parsing / post-processing — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`CodeResponse.extractCodeFromResponse`, with `lang = LANG.lowercase()`:

```kotlin
if ("```$lang" in content) {
    pattern = Pattern.compile("```$lang(.*?)```", DOTALL)
    if (matcher.find()) matcher.group(1).trim()
    else content.replace("```$lang", "").replace("```", "")
} else content
```

Three behaviours worth noting:

1. only the **first** ` ```java ` block is taken;
2. a bare ` ``` ` fence with no language tag is **not** detected — the entire
   response, prose included, is returned as the "code";
3. no AST validation, no per-test splitting at this stage. Test methods are
   counted later by `YateCodeUtils.countTestMethods`, and generation is deemed
   failed if that count is 0.

Post-generation, `generateTestUsingModel` sets the test class's package and
copies the imports of the class under test.

`removeNonCompilingTests` / `removeNonPassingTests` exist on the abstract runner
but are called **only from the `HYBRID` branch**, so at `CLASS` level — the level
Plain uses — they never run. `REMOVE_NON_PASSING_TESTS=true` in `.env.dev` is
therefore not in force for the Plain workflow.

## L. When the workflow stops — `CONFIRMED_PUBLIC_IMPLEMENTATION`

`YateAbstractRunner.generate(classPath, TestLevel.CLASS)`:

1. abort before generating unless the repository's existing suite is green
   (`requireGreenSuiteOnGeneration = true`);
2. one generation request → write the test class to disk;
3. `onValidation`: `fixGeneratedTestClass` (the ≤5 repair loop), write; then
   `fixOraclesInTestClass` (no-op); write;
4. **failure if `countTestMethods() <= 0`**, or if any exception was thrown;
5. `enhanceCoverage` → `null` for Plain → nothing added;
6. the generated test class is moved to the output directory.

So one program under test costs **1 generation request + at most 5 repair
requests**, and terminates when the tests report no errors, or after 5 repair
attempts, whichever comes first.

## Execution architecture for generated tests (design; nothing generated yet)

Generated tests are arbitrary untrusted code, so they run only in a child
process, reusing `target.run_streamed_worker` — the same streaming worker with
per-item `SIGALRM` and stall detection that mutation testing and fault
classification already use. Nothing new is needed there.

Two facts must be kept apart per generated test, because Table IV reports them
as different metrics:

| Fact | Question | Feeds |
|---|---|---|
| **triggered** | do the test's *inputs* make the faulty program behave differently from the reference? | FTR |
| **detected** | does the LLM's *assertion* actually fail on the faulty program? | FDR |

They come apart in practice: a test can call the function with a fault-revealing
input and then assert something too weak to notice (triggered, not detected), and
in principle assert something wrong that fails for an unrelated reason (detected
without triggering, which must be reported, not silently counted as detection).

**Planned mechanism, and why no AST oracle extractor is needed.** Rather than
parsing assertions out of the test source — brittle, and it would have to
understand every assertion form an LLM might emit — instrument the *entry point*:

1. Run the test function against the **reference** program with the entry point
   wrapped by a recorder. The wrapper appends each call's arguments to a list and
   delegates. This yields the exact input tuples the test exercises, whatever
   syntax the assertions use.
2. Feed those recorded tuples to the machinery that already exists
   (`_output_oracle.compare_outputs` via the classification worker) to decide
   **triggered** — identical semantics to the fault labelling already done, so
   trigger decisions stay consistent across the project.
3. Separately, run the test function against the **faulty** program and record
   whether it raises `AssertionError` (or any exception). That is **detected**.

Step 3 alone gives FDR; steps 1-2 give FTR; neither depends on reading the
assertion text. A test whose recorded input list is empty (it never called the
entry point) is reported as such rather than counted either way.

`TestCaseRecord.runnable_module` already emits `preamble + single test` as a
standalone module, which is the unit both steps execute.

This is a design note. No such runner is implemented yet, and no test has been
generated to run through it.

## Verification of the specific claims put to us

| Claim | Verdict | Evidence |
|---|---|---|
| `YatePlainRunner` default `maxFixIterations` is 5 | **Confirmed** | `private val maxFixIterations: Int = 5` |
| `ChatOpenAIModel` uses temperature 0.1 | **Confirmed** | `temperature = 0.1`, hard-coded in `executeRequest` |
| Plain-LLM performs no coverage-enhancement phase | **Confirmed** | `enhanceCoverageForClass` returns `null` |
| `SimpleUnitTestGenerator` uses the simple generation prompts | **Confirmed** | reads `ablation_generate_simple`, `ablation_generate_simple_method_named`, `ablation_generate_simple_constructors_named` |
| Repair uses execution/compilation errors and conversation history | **Confirmed** | `runTestsForErrors(..., includeCompilingTests = true)` + `model.ask(prompts, history = response.conversation)`; docstring says "compile or oracle errors" |

One correction to the framing of the last claim: repair uses compile errors
**and failing assertions**, not compile errors alone.

## Target paper vs public Java implementation

| Aspect | Target Table-IV paper | Public YATE Java implementation | Python reconstruction decision | Confidence |
|---|---|---|---|---|
| Tool identity | "LLM-Plain's default configuration" | YATE `YatePlainRunner` + `SimpleUnitTestGenerator` | Reimplement the same shape in Python | Medium — same tool family, different language |
| Language / framework | Python (HumanEval) | `LANG=Java`, `TEST_FRAMEWORK=Junit5` | Python + plain `test_*` assert functions (see below) | `INFERRED_ADAPTATION` |
| Generation prompt | not published | `ablation_generate_simple.txt`, one message | Minimal semantic translation, one message | Medium |
| System prompt | not published | "You are a tool used by Java Developers to generate tests." | Same sentence with Python | Medium |
| Temperature | not stated | 0.1, hard-coded | Adopt 0.1, tagged `INFERRED_ADAPTATION` | **UNKNOWN for the paper** |
| Repair iterations | not stated | 5 | Adopt 5, tagged `INFERRED_ADAPTATION` | **UNKNOWN for the paper** |
| Generation requests per program | not stated | 1 (`MAX_GENERATE_ITERATIONS=1`) | 1 | **UNKNOWN for the paper** |
| Program under test in prompt | not stated | Yes — full class content | Yes | **UNKNOWN for the paper** |
| Coverage feedback | not stated for Plain | None | None | Medium-high (it is what "Plain" means) |
| Oracle repair | not stated | Same loop as compile errors | Configurable; default off for faulty programs, and flagged | **UNKNOWN for the paper**, and consequential |
| Test extraction | not stated | First ` ```lang ` block, else whole response | First fenced block, bare fences handled, then split per `test_*` function | `INFERRED_ADAPTATION` |
| Green-suite precondition | n/a | Required before generating | Not applicable to HumanEval single-file tasks | `INFERRED_ADAPTATION` |
| Test-generation model | **UNKNOWN** | Any OpenAI/DeepSeek/Mistral model by name; `.env.dev` default `gpt-4.1` | Must be chosen and recorded explicitly | **UNKNOWN** |

### Parameters still UNKNOWN for Table IV

Every one of these is required to recreate the 4,872 HumanEval LLM-Plain tests,
and none is answered by the paper text available to us or by the public Java
code:

1. **Which LLM generated the Table IV tests.** UNKNOWN.
2. **Whether the same generation model was used for every faulty program.** UNKNOWN.
3. **Whether the faulty implementation was in the generation prompt.** UNKNOWN
   for the paper. The Java code does include the class under test, and a
   differential-testing design would require it, but that is inference.
4. **Generation requests per faulty implementation.** UNKNOWN. Java uses 1.
5. **Tests produced per request.** UNKNOWN — the prompt asks for "all tests
   needed", so the count is whatever the model emits.
6. **Temperature.** UNKNOWN for the paper. Java hard-codes 0.1. Note the *code
   generation* in the paper used temperature 0.8; there is no basis for assuming
   test generation reused it.
7. **Repair iterations.** UNKNOWN for the paper. Java default 5.
8. **Python test framework / syntax requested.** UNKNOWN.
9. **How individual tests were extracted from one response.** UNKNOWN.
10. **How invalid generated tests were filtered or repaired.** UNKNOWN. In the
    Java Plain path, non-passing tests are *not* removed and failures are sent
    back for repair — a policy that, applied to faulty programs, would suppress
    exactly the assertions FDR is meant to measure.
11. **Whether each selected fault has its own pool.** See below.
12. **The prompt text itself.** UNKNOWN; the Java prompt is our only model.

## The 4,872 / 520 arithmetic

```
4872 / 520 = 9.3692...
```

**What it can tell us.** The mean number of tests per selected HumanEval fault is
about 9.4, across all fault-generating models. It is a useful order-of-magnitude
check: a reconstruction producing ~1 or ~100 tests per fault is not behaving like
the paper's, and one producing ~9-10 on average is at least consistent.

**What it cannot tell us.** It is a ratio of two aggregates, so it constrains
only the total:

- it does **not** imply any fault has 9 or 10 tests. A mean of 9.37 is equally
  consistent with every fault having 9-10 tests and with half having 2 and half
  having 17;
- it does not say the distribution is unimodal, or even that every fault has a
  non-empty pool;
- 520 is the selected-fault count **across all models**, and 4,872 is the
  HumanEval test count; if either aggregate spans a different set than the other
  (e.g. tests counted before fault selection), the ratio is not a per-fault mean
  at all;
- it says nothing about how many *generation requests* produced those tests, nor
  how many were discarded before counting.

We will not assume 9 or 10 tests per fault. When a reconstruction runs, we will
report our own mean and distribution beside 9.37 and let the comparison stand
on its own.

## Does `TS_f` imply per-fault pools?

**Evidence for (strongest interpretation).** The protocol sentence quoted to us
reads *"Given a faulty implementation `f` and the pool of generated tests
`TS_f`..."*. The pool is **subscripted by `f`**, which in standard notation means
one pool per faulty implementation. The sampling procedure also samples per
fault and per criterion, and adequacy is measured on the program under test
(assumption A1), which only makes sense if the pool is aligned to that program.
Under the Java workflow the generator is invoked *per class under test*, so a
per-program pool is what the tool naturally produces.

**Competing interpretations, not excluded.**

1. **Per-task pool, reused across that task's faults.** `TS_f` could be
   shorthand for "the pool associated with the task `f` belongs to", generated
   once from the prompt or the reference. 520 faults over 164 HumanEval tasks is
   ~3.2 faults per task; if pools were per task, the 4,872 tests would be ~29.7
   per task, and pools would be shared by sibling faults.
2. **One pool per (task, generating model).** Plausible if generation was run per
   model batch.
3. **A single global pool per benchmark**, filtered per fault. Hard to reconcile
   with the subscript, but not refuted by the numbers.

**Confidence: medium-high for per-fault pools**, on the strength of the
subscript and the per-program generator invocation — *not* high, because we hold
no artifact and no explicit sentence stating it. This is recorded as an open
question, and the code keeps pools keyed by fault id so either reading can be
represented.

## Adequacy target: faulty implementation, reference, or something else?

Re-examined for this phase. **Honest statement of what we have:** no copy of the
paper is present in this repository, and no full-text access was used in this
phase. The evidence available is the protocol wording quoted into this project's
own notes and `docs/experiment_plan.md`.

Evidence that adequacy is measured on the **faulty implementation** `f`:

1. The protocol opens *"Given a faulty implementation `f` and the pool of
   generated tests `TS_f`"* and then samples tests by whether they increase
   criterion `C`. The subject of the procedure is `f`.
2. Statement, branch and mutation adequacy are all properties of a concrete
   program text. The only program named in the procedure is `f`.
3. Mutation adequacy in particular requires mutants of *some* program; mutating
   the reference while testing `f` would measure the wrong artifact.
4. FTR asks whether the sampled suite triggers the fault *in `f`* — the suite and
   the fault are tied to the same program.

Evidence that would point the other way, and its status:

- If the paper computed coverage once on the reference and reused it for all
  faults of a task, adequacy items would be comparable across faults. We have
  seen no wording suggesting this, but we cannot exclude it.
- A shared instrumented harness (e.g. coverage of the prompt's signature only)
  would also be reference-based. No evidence for it.

**Confidence: high** that `f` is the adequacy target, on internal-consistency
grounds. **No change is made to the implementation.** Assumption A1 stands as
written, and it remains labelled an assumption rather than a verified fact. If
full paper text or a replication package later contradicts it, only the call
sites change: `sampling.py` consumes opaque `adequacy_items`.
