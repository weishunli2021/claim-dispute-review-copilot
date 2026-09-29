# Success Metrics (Prototype Assumptions)

> **These are prototype-stage design targets used to guide the evaluation harness that will be
> built later (`evals/`). They are illustrative assumptions for an interview/portfolio project,
> not measured results, not SLAs, and not production requirements.** No production system
> commitments should be inferred from these numbers.

| Metric | Prototype target |
| --- | --- |
| Task success rate | >= 85% |
| Tool selection accuracy | >= 90% |
| Retrieval recall | >= 90% |
| Grounded answer rate | >= 95% |
| Unsupported claim rate | <= 5% |
| Escalation recall | >= 95% |
| Average latency | < 5 seconds |

## Definitions (for later use when the eval harness is built)

- **Task success rate** — fraction of golden-set cases where the system's final recommended
  action matches the expected action.
- **Tool selection accuracy** — fraction of steps where the orchestrator invoked the correct
  tool(s)/skill(s) for the situation.
- **Retrieval recall** — fraction of golden-set cases where all necessary supporting
  documents/facts were present in the retrieved context.
- **Grounded answer rate** — fraction of answers where every factual claim traces back to
  retrieved/tool-provided evidence.
- **Unsupported claim rate** — fraction of individual factual claims in answers that are not
  traceable to evidence (the complement of grounding, measured at the claim level rather than
  the answer level).
- **Escalation recall** — fraction of cases that *should* have been escalated to a human that
  the system actually escalated (missing an escalation is worse than an unnecessary one).
- **Average latency** — average wall-clock time from question to answer/escalation.

These targets and definitions are expected to be refined once the golden set and evaluation
harness exist.
