import requests

BASE_URL = "http://localhost:8000"

test_cases = [
    {
        "question": "How many vacation days do employees get?",
        "expected_facts": ["15"],
        "expected_doc": "policy.txt",
    },
    {
        "question": "What are the four gates for entry?",
        "expected_facts": ["4H/1H Zone", "displacement", "retest"],
        "expected_doc": "Reggie_MGC_Trading_Strategy.pdf",
    },
    {
        "question": "What is the company's policy on space travel benefits?",
        "expected_facts": [],
        "expected_doc": None,
        "expect_insufficient_context": True,
    },
]


def says_insufficient_context(answer: str) -> bool:
    answer_lower = answer.lower()
    phrases = [
        "don't know",
        "do not know",
        "not in the context",
        "insufficient context",
        "not enough information",
        "cannot determine",
    ]
    return any(phrase in answer_lower for phrase in phrases)


passed_questions = 0

for index, case in enumerate(test_cases, start=1):
    print(f"\n{'=' * 70}")
    print(f"Question {index}: {case['question']}")

    try:
        response = requests.post(
            f"{BASE_URL}/ask",
            json={"question": case["question"]},
            timeout=60
        )
        result = response.json()
    except Exception as exc:
        print(f"[ERROR] Unexpected failure: {exc}")
        continue

    answer = result.get("answer", "")
    sources = result.get("sources", [])

    print(f"Answer: {answer[:300]}")
    print(f"Sources: {[s.get('document_name') for s in sources]}")

    checks = []

    if case.get("expect_insufficient_context"):
        checks.append((
            "Correctly says context is insufficient",
            says_insufficient_context(answer),
        ))
    else:
        for fact in case["expected_facts"]:
            checks.append((
                f"Contains expected fact: '{fact}'",
                fact.lower() in answer.lower(),
            ))

        expected_doc = case.get("expected_doc")
        if expected_doc:
            actual_docs = [s.get("document_name") for s in sources]
            checks.append((
                f"Cites expected source: {expected_doc}",
                expected_doc in actual_docs,
            ))

    question_passed = all(check_passed for _, check_passed in checks)

    for check_name, check_passed in checks:
        status = "PASS" if check_passed else "FAIL"
        print(f"[{status}] {check_name}")

    if question_passed:
        passed_questions += 1

total_questions = len(test_cases)
accuracy = (passed_questions / total_questions) * 100

print(f"\n{'=' * 70}")
print(
    f"Overall: {passed_questions}/{total_questions} questions fully passed "
    f"({accuracy:.0f}%)"
)
