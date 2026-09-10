from evalplus.data import get_human_eval_plus, get_human_eval_plus_hash


def main():
    problems = get_human_eval_plus()
    dataset_hash = get_human_eval_plus_hash()

    print(f"HumanEval+ tasks: {len(problems)}")
    print(f"Dataset hash: {dataset_hash}")

    task_id = "HumanEval/0"
    problem = problems[task_id]

    print(f"\nTask: {task_id}")
    print(f"Entry point: {problem['entry_point']}")

    print("\nAvailable fields:")
    for key in problem:
        print(f"  - {key}")

    print("\nPrompt:")
    print(problem["prompt"])

    print("\nCanonical solution:")
    print(problem["canonical_solution"])

    print(f"\nBase inputs: {len(problem['base_input'])}")
    print(f"Plus inputs: {len(problem['plus_input'])}")

    print("\nFirst 3 base inputs:")
    for test_input in problem["base_input"][:3]:
        print(test_input)

    print("\nFirst 3 plus inputs:")
    for test_input in problem["plus_input"][:3]:
        print(test_input)


if __name__ == "__main__":
    main()
