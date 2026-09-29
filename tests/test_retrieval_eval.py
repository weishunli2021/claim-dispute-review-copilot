"""Tests for the retrieval golden-set evaluation harness: Hit@k, SectionRecall@k,
and Precision@k, computed correctly and executable for every configuration."""

from __future__ import annotations

from evals.retrieval_eval import evaluate_configuration, filter_by_tag, load_golden_set, summarize
from rag.chunking import SMALL


def test_golden_set_loads_and_has_expected_shape():
    golden_set = load_golden_set()
    assert 15 <= len(golden_set) <= 25
    for item in golden_set:
        assert item["query_id"]
        assert item["query"]
        assert item["expected_relevant_sections"]


def test_golden_set_includes_paraphrase_and_ambiguous_tags():
    golden_set = load_golden_set()
    tags = {tag for item in golden_set for tag in item.get("tags", [])}
    assert "paraphrase" in tags
    assert "ambiguous" in tags


def test_evaluate_configuration_executes_and_produces_valid_metric_families():
    golden_set = load_golden_set()
    results = evaluate_configuration("tfidf", SMALL, golden_set)
    assert len(results) == len(golden_set)

    summary = summarize(results)
    assert set(summary.keys()) == {"hit", "section_recall", "precision"}
    for family in summary.values():
        assert set(family.keys()) == {1, 3, 5}
        for value in family.values():
            assert 0.0 <= value <= 1.0

    # More chances to find a hit / more expected sections found as k grows.
    assert summary["hit"][1] <= summary["hit"][3] <= summary["hit"][5]
    assert summary["section_recall"][1] <= summary["section_recall"][3] <= summary["section_recall"][5]


def test_section_recall_reflects_partial_multi_section_matches():
    # A synthetic single-query golden set with two expected sections, where
    # only one can realistically be found -- SectionRecall should be able to
    # register a partial (non-0, non-1) score distinct from Hit's binary one.
    golden_set = [
        {
            "query_id": "synthetic_multi_section",
            "query": "Does outpatient knee MRI require prior authorization?",
            "expected_relevant_sections": ["IMG-2", "ZZZ-NOT-A-REAL-SECTION"],
        }
    ]
    results = evaluate_configuration("tfidf", SMALL, golden_set)
    result = results[0]
    assert result.hit_at_k[5] is True
    assert 0.0 < result.section_recall_at_k[5] < 1.0


def test_precision_handles_fewer_than_k_results_returned(monkeypatch):
    import evals.retrieval_eval as retrieval_eval_module

    def _fake_search_policy(query, top_k, config, provider_name):
        return []  # simulate an index/provider that returns nothing

    monkeypatch.setattr(retrieval_eval_module, "search_policy", _fake_search_policy)

    golden_set = [
        {"query_id": "no_results", "query": "anything", "expected_relevant_sections": ["IMG-2"]}
    ]
    results = evaluate_configuration("tfidf", SMALL, golden_set)
    result = results[0]
    for k in (1, 3, 5):
        assert result.precision_at_k[k] == 0.0
        assert result.hit_at_k[k] is False


def test_paraphrase_subset_executes_for_semantic_configuration():
    golden_set = load_golden_set()
    results = evaluate_configuration("semantic", SMALL, golden_set)
    paraphrase_results = filter_by_tag(results, "paraphrase")
    assert paraphrase_results
    summary = summarize(paraphrase_results)
    assert 0.0 <= summary["hit"][5] <= 1.0
