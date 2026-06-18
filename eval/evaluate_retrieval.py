import requests

BASE_URL = "http://localhost:8000"

test_cases = [
    {
        "question": "What are the four gates for entry?",
        "expected_doc": "Reggie_MGC_Trading_Strategy.pdf"
    },
    {
        "question": "Which prop firm is the primary funded destination?",
        "expected_doc": "Reggie_MGC_Trading_Strategy.pdf"
    },
    {
        "question": "How many vacation days do employees get?",
        "expected_doc": "policy.txt"
    },
    {
        "question": "What happens if an employee harasses a coworker?",
        "expected_doc": "policy.txt"
    },
    {
        "question": "When do health benefits begin?",
        "expected_doc": "policy.txt"
    },
    {
        "question": "What is the stop loss rule for the trading strategy?",
        "expected_doc": "Reggie_MGC_Trading_Strategy.pdf"
    },
]

passed = 0
for i, case in enumerate(test_cases, 1):
    response = requests.post(
        f"{BASE_URL}/debug-search",
        json={"question": case["question"]}
    )
    result = response.json()

    if result["results"]:
        top_doc = result["results"][0]["document_name"]
    else:
        top_doc = None

    status = "PASS" if top_doc == case["expected_doc"] else "FAIL"
    if status == "PASS":
        passed += 1

    print(f"Question {i}: {status} (expected {case['expected_doc']}, got {top_doc})")

accuracy = (passed / len(test_cases)) * 100
print(f"\nRetrieval Accuracy: {accuracy:.0f}%")
