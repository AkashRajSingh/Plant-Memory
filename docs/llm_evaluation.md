# Where the LLM helps: rules vs. Groq gpt-oss-120b

Measured on the synthetic export (400 work orders, seed 7) against generator ground truth, Oct 2026.
The model is `openai/gpt-oss-120b` on the Groq free tier with low reasoning effort, run on 399 of 400 orders.

## Finding

As an independent extractor, the LLM is less accurate than the plant-specific rules on every field. Its value is as a **second opinion**: filling gaps the rules leave, and flagging disagreements for technician review.

| Field | Rules accuracy | LLM accuracy | Where they disagree | Where the rules found nothing |
| --- | --- | --- | --- | --- |
| Component | 96.2% | 87.2% | rules right 19, LLM right 5 | LLM right 1, wrong 4 |
| Failure mode | 88.9% | 55.4% | rules right 24, LLM right 3 | LLM right 2 |
| Root cause | 62.3% | 46.9% | none | LLM right 1 |
| Action | 93.0% | 92.5% | rules right 3 | LLM right 8, wrong 2 |
| Symptom | 59.7% | 53.5% | rules right 3 | LLM right 5, wrong 2 |

## Merge policy adopted

1. Structured SAP columns always win.
2. On disagreement, the rules keep the value. Confidence drops by 0.15 and the fact gets the note "LLM suggested X; check", so it rises in the verification queue.
3. The LLM fills empty fields for failure mode, root cause, action and symptom, capped at confidence 0.7. It never fills components.
4. The LLM's "other:" answers count as drift only where the rules found nothing.
5. The LLM cannot turn a failure into planned work. It reads Work Type = Preventive literally, which removed 99 genuine wear failures in the first merge.

## Result (rules-only vs. merged)

| Metric | Rules only | Rules + LLM (adopted policy) | First merge (LLM could override) |
| --- | --- | --- | --- |
| Component accuracy | 96.2% | 96.2% | 93.4% |
| Failure mode accuracy | 88.9% | 88.9% | 82.9% |
| Action accuracy | 93.0% | 95.0% | 94.8% |
| Symptom accuracy | 59.7% | 61.1% | 61.1% |
| Diagnosis top-1 | 82.1% | 82.1% | 75.0% |

## Caveat for product decisions

The rules were written alongside the synthetic data's vocabulary, so this comparison flatters them. On a real plant export, with unseen abbreviations and Hinglish remarks, expect the rules to miss more and gap-filling to matter more. Re-run this comparison on 50 to 100 hand-labelled real orders before choosing the merge policy for production.
