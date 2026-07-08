# Scorecard — faithfulness-judge-validation (RAGTruth)

- judge `MiniCheck-Flan-T5-Large` (<1B) | device `cpu` | git `cb2e95c` | seed 42
- dataset RAGTruth test split (ungated GitHub); 120 responses, 20 per
  (task_type × grounded/hallucinated) cell | threshold 0.5 | ~35 min CPU

## Judge vs human hallucination labels

Positive class = grounded. Response support = MIN over sentence-level supports
("any unsupported claim -> hallucinated"). AUROC is threshold-free ranking
quality; BA is balanced accuracy at the 0.5 support threshold.

| task | AUROC | BA@0.5 | n (hallucinated) |
|---|---|---|---|
| **QA** (Munin's task) | **0.950** | 0.725 | 40 (20) |
| Summary | 0.708 | 0.650 | 40 (20) |
| Data2txt | 0.723 | 0.525 | 40 (20) |
| overall | 0.746 | 0.633 | 120 (60) |

**Gate (QA-AUROC >= 0.70): 0.950 -> flan-t5-large-sufficient.** No escalation to
MiniCheck-7B. QA (RAG question-answering) is Munin's use case and matches the
model's published GPT-4-comparable grounding accuracy. Data2txt's weak BA@0.5
(0.525) is a threshold artifact on a non-Munin task type (AUROC 0.723 shows the
ranking still separates); the 0.5 operating point can be tuned per-task later if
those tasks ever matter. Judge validated for Track B.
