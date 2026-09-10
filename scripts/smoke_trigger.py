from copy import deepcopy

from evalplus.data import get_human_eval_plus


def build_function(source, entry_point):
    namespace = {}
    exec(source, namespace)
    return namespace[entry_point]


def main():
    problems = get_human_eval_plus()
    problem = problems["HumanEval/0"]

    entry_point = problem["entry_point"]

    # Official HumanEval+ reference implementation
    reference_source = problem["prompt"] + problem["canonical_solution"]

    # Deliberately faulty implementation used ONLY to test our pipeline.
    faulty_source = problem["prompt"] + "\n    return False\n"

    reference_fn = build_function(reference_source, entry_point)
    faulty_fn = build_function(faulty_source, entry_point)

    all_inputs = problem["base_input"] + problem["plus_input"]

    triggering_inputs = []

    for index, args in enumerate(all_inputs):
        reference_output = reference_fn(*deepcopy(args))
        faulty_output = faulty_fn(*deepcopy(args))

        if reference_output != faulty_output:
            triggering_inputs.append(
                (index, args, reference_output, faulty_output)
            )

    print(f"Task: HumanEval/0")
    print(f"Entry point: {entry_point}")
    print(f"Total test inputs: {len(all_inputs)}")
    print(f"Triggering inputs: {len(triggering_inputs)}")

    print("\nFirst 5 triggering examples:")

    for index, args, reference_output, faulty_output in triggering_inputs[:5]:
        print(f"\nTest #{index}")
        print(f"Input: {args}")
        print(f"Reference output: {reference_output}")
        print(f"Faulty output:    {faulty_output}")


if __name__ == "__main__":
    main()
