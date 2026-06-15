from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
from datasets import Dataset
import requests

BASE_URL = "http://localhost:8000"

eval_questions = [
    {
        "question": "How many vacation days do employees get?",
        "ground_truth": "Employees receive 15 vacation days per year."
    },
    {
        "question": "What is the remote work policy?",
        "ground_truth": "Employees may work remotely up to 3 days per week."
    },
    {
        "question": "When do benefits begin?",
        "ground_truth": "Benefits begin on the first day of employment."
    },
    {
        "question": "What happens if an employee harasses a coworker?",
        "ground_truth": "Harassment of any kind will result in immediate termination."
    },
    {
        "question": "How much of health insurance does the company cover?",
        "ground_truth": "The company covers 80% of health insurance premiums."
    }
]

questions = []
answers = []
contexts = []
ground_truths = []

for item in eval_questions:
    response = requests.post(
        f"{BASE_URL}/ask",
        json={"question": item["question"]}
    )
    result = response.json()
    questions.append(item["question"])
    answers.append(result["answer"])
    contexts.append(result["retrieved_chunks"])
    ground_truths.append(item["ground_truth"])

dataset = Dataset.from_dict({
    "question": questions,
    "answer": answers,
    "contexts": contexts,
    "ground_truth": ground_truths
})

results = evaluate(
    dataset,
    metrics=[faithfulness, answer_relevancy, context_precision, context_recall]
)

print(results)
