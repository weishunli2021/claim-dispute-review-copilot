"""Evaluate retrieval quality against evals/retrieval_golden_set.json.

This evaluates the *retriever*, not an LLM-generated answer -- there is no
answer generation in this stage. Three metric families are reported, each
answering a different question:

- Hit@k: did AT LEAST ONE expected-relevant section appear among the
  top-k retrieved chunks? (0 or 1 per query.) This was previously named
  "Recall@k" in this file -- that name was wrong (it is the standard
  "hit rate@k" simplification of recall, not proportional recall) and has
  been corrected. Averaged across queries, it answers "how often does the
  associate see at least one right answer in the first k results?"

- SectionRecall@k: of a query's expected-relevant sections, what
  FRACTION appeared among the top-k retrieved chunks' sections? This is
  true recall: (expected sections found in top-k) / (total expected
  sections). A query with 3 expected sections that surfaces only 1 in the
  top-5 scores 0.33 here, even though it would score a "hit" (1.0) under
  Hit@5. Averaged across queries.

- Precision@k: of the (up to) k chunks actually retrieved, what fraction
  belong to an expected-relevant section? When fewer than k chunks exist
  to retrieve, the denominator is the number actually returned, not the
  nominal k, so a short result list is not penalized as if the missing
  slots were wrong. Averaged across queries.

Run all four (embedding provider x chunking config) combinations and
compare:

    python -m evals.retrieval_eval
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from rag.chunking import LARGE, SMALL, ChunkingConfig
from rag.retriever import search_policy

GOLDEN_SET_PATH = Path(__file__).resolve().parent / "retrieval_golden_set.json"
K_VALUES = (1, 3, 5)
MAX_K = max(K_VALUES)

CONFIGURATIONS: list[tuple[str, ChunkingConfig]] = [
    ("tfidf", SMALL),
    ("tfidf", LARGE),
    ("semantic", SMALL),
    ("semantic", LARGE),
]


@dataclass
class QueryResult:
    query_id: str
    query: str
    tags: list[str]
    expected_sections: list[str]
    retrieved_sections_by_k: dict[int, list[str]]
    hit_at_k: dict[int, bool]
    section_recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]


def load_golden_set(path: Path | None = None) -> list[dict]:
    """Load the golden-set queries. Each item has query_id, query,
    expected_relevant_sections, and an optional tags list (e.g.
    ["paraphrase"], ["ambiguous"])."""
    return json.loads((path or GOLDEN_SET_PATH).read_text(encoding="utf-8"))


def evaluate_configuration(
    provider_name: str,
    config: ChunkingConfig,
    golden_set: list[dict] | None = None,
) -> list[QueryResult]:
    """Run every golden-set query against (provider_name, config)'s index
    and score Hit@k, SectionRecall@k, and Precision@k at each k."""
    golden_set = golden_set if golden_set is not None else load_golden_set()

    results: list[QueryResult] = []
    for item in golden_set:
        retrieved = search_policy(
            item["query"], top_k=MAX_K, config=config, provider_name=provider_name
        )
        expected = set(item["expected_relevant_sections"])
        total_expected = len(expected)

        retrieved_sections_by_k: dict[int, list[str]] = {}
        hit_at_k: dict[int, bool] = {}
        section_recall_at_k: dict[int, float] = {}
        precision_at_k: dict[int, float] = {}

        for k in K_VALUES:
            top_k_chunks = retrieved[:k]
            top_k_sections = [chunk.section_id for chunk in top_k_chunks]
            retrieved_sections_by_k[k] = top_k_sections

            found_expected = expected.intersection(top_k_sections)
            hit_at_k[k] = bool(found_expected)
            section_recall_at_k[k] = (len(found_expected) / total_expected) if total_expected else 0.0

            actual_returned = len(top_k_chunks)  # may be < k; see module docstring
            if actual_returned == 0:
                precision_at_k[k] = 0.0
            else:
                relevant_count = sum(1 for s in top_k_sections if s in expected)
                precision_at_k[k] = relevant_count / actual_returned

        results.append(
            QueryResult(
                query_id=item["query_id"],
                query=item["query"],
                tags=item.get("tags", []),
                expected_sections=sorted(expected),
                retrieved_sections_by_k=retrieved_sections_by_k,
                hit_at_k=hit_at_k,
                section_recall_at_k=section_recall_at_k,
                precision_at_k=precision_at_k,
            )
        )
    return results


def summarize(results: list[QueryResult]) -> dict[str, dict[int, float]]:
    """Return {"hit": {k: mean}, "section_recall": {k: mean}, "precision": {k: mean}}."""
    n = len(results)
    if n == 0:
        return {
            "hit": {k: 0.0 for k in K_VALUES},
            "section_recall": {k: 0.0 for k in K_VALUES},
            "precision": {k: 0.0 for k in K_VALUES},
        }
    return {
        "hit": {k: sum(r.hit_at_k[k] for r in results) / n for k in K_VALUES},
        "section_recall": {k: sum(r.section_recall_at_k[k] for r in results) / n for k in K_VALUES},
        "precision": {k: sum(r.precision_at_k[k] for r in results) / n for k in K_VALUES},
    }


def filter_by_tag(results: list[QueryResult], tag: str) -> list[QueryResult]:
    return [r for r in results if tag in r.tags]


def print_report(provider_name: str, config_name: str, results: list[QueryResult]) -> None:
    summary = summarize(results)
    print(f"=== {provider_name} + {config_name} ({len(results)} queries) ===")
    for family, label in (("hit", "Hit"), ("section_recall", "SectionRecall"), ("precision", "Precision")):
        line = "  " + " ".join(f"{label}@{k}={summary[family][k]:.2%}" for k in K_VALUES)
        print(line)

    failures = [r for r in results if not r.hit_at_k[MAX_K]]
    if failures:
        print(f"  Missed even at k={MAX_K} ({len(failures)}):")
        for r in failures:
            tag_note = f" [{', '.join(r.tags)}]" if r.tags else ""
            print(f"    [{r.query_id}]{tag_note} {r.query!r}")
            print(f"        expected:  {r.expected_sections}")
            print(f"        retrieved: {r.retrieved_sections_by_k[MAX_K]}")
    print()


def print_comparison_table(all_results: dict[tuple[str, str], list[QueryResult]]) -> None:
    print("=== Comparison across all four configurations ===")
    header = f"{'config':<16}" + "".join(f"{'Hit@'+str(k):>9}" for k in K_VALUES)
    header += "".join(f"{'SecRec@'+str(k):>10}" for k in K_VALUES)
    header += "".join(f"{'Prec@'+str(k):>9}" for k in K_VALUES)
    print(header)
    for (provider_name, config_name), results in all_results.items():
        summary = summarize(results)
        row = f"{provider_name + '/' + config_name:<16}"
        row += "".join(f"{summary['hit'][k]:>9.1%}" for k in K_VALUES)
        row += "".join(f"{summary['section_recall'][k]:>10.1%}" for k in K_VALUES)
        row += "".join(f"{summary['precision'][k]:>9.1%}" for k in K_VALUES)
        print(row)
    print()


def print_tagged_subset_report(all_results: dict[tuple[str, str], list[QueryResult]], tag: str) -> None:
    print(f"=== '{tag}' subset across all four configurations ===")
    for (provider_name, config_name), results in all_results.items():
        subset = filter_by_tag(results, tag)
        if not subset:
            continue
        summary = summarize(subset)
        print(
            f"  {provider_name}/{config_name} ({len(subset)} queries): "
            + " ".join(f"Hit@{k}={summary['hit'][k]:.0%}" for k in K_VALUES)
        )
        for r in subset:
            marker = "OK" if r.hit_at_k[MAX_K] else "MISS"
            print(f"      [{marker}] {r.query_id}: {r.retrieved_sections_by_k[MAX_K]} (expected {r.expected_sections})")
    print()


def _main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Hit@1/3/5, SectionRecall@1/3/5, and Precision@1/3/5 against the "
            "golden set, across all four (embedding provider x chunking config) combinations."
        )
    )
    parser.parse_args(argv)

    golden_set = load_golden_set()
    all_results: dict[tuple[str, str], list[QueryResult]] = {}
    for provider_name, config in CONFIGURATIONS:
        results = evaluate_configuration(provider_name, config, golden_set)
        all_results[(provider_name, config.name)] = results
        print_report(provider_name, config.name, results)

    print_comparison_table(all_results)
    print_tagged_subset_report(all_results, "paraphrase")
    print_tagged_subset_report(all_results, "ambiguous")


if __name__ == "__main__":
    _main(sys.argv[1:])
