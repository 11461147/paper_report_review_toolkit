"""Regression tests for the bundled academic-search helper."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills/paper-deep-researcher/scripts/paper_research_skill.py"
SPEC = importlib.util.spec_from_file_location("paper_research_skill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
AcademicResearchSkill = MODULE.AcademicResearchSkill


class AcademicResearchSkillTests(unittest.TestCase):
    def test_chinese_character_bigrams_match_unsegmented_text(self) -> None:
        self.assertEqual(
            AcademicResearchSkill.multilingual_tokens("圖譜檢索"),
            ["圖譜", "譜檢", "檢索"],
        )
        self.assertEqual(
            AcademicResearchSkill.lexical_candidate_score(
                "圖譜檢索", "本文提出新的圖譜檢索方法。"
            ),
            1.0,
        )

    def test_negation_is_a_warning_not_a_relevance_penalty(self) -> None:
        passage = "The method did not improve accuracy on Dataset A."
        self.assertTrue(AcademicResearchSkill.has_negation_warning(passage))
        self.assertGreater(
            AcademicResearchSkill.lexical_candidate_score("improve accuracy", passage),
            0,
        )

    def test_unknown_citation_metrics_have_no_impact_signal(self) -> None:
        helper = AcademicResearchSkill()
        self.assertIsNone(helper._calculate_impact_score({
            "year": date.today().year,
            "citationCount": None,
            "influentialCitationCount": None,
        }))

    @patch("paper_research_skill.requests.get")
    def test_arxiv_fallback_preserves_unknown_metrics(self, get: Mock) -> None:
        response = Mock(status_code=200)
        response.text = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Graph Retrieval</title><summary>A graph retrieval method.</summary>
          <published>2025-01-01T00:00:00Z</published>
          <id>https://arxiv.org/abs/2501.00001</id>
          <author><name>Ada Example</name></author>
        </entry></feed>"""
        get.return_value = response

        paper = AcademicResearchSkill().search_arxiv_papers("graph retrieval")[0]
        self.assertIsNone(paper["citationCount"])
        self.assertIsNone(paper["influentialCitationCount"])
        self.assertEqual(paper["citation_metrics_status"], "unavailable")
        self.assertIsNone(paper["citation_impact_signal"])

    def test_snippets_are_ranked_without_an_arbitrary_cutoff(self) -> None:
        helper = AcademicResearchSkill()
        helper._api_request_with_backoff = Mock(return_value={
            "paperId": "p1",
            "title": "Example",
            "year": 2025,
            "authors": [],
            "abstract": "A remotely related passage.",
            "tldr": None,
        })
        snippets = helper.get_paper_snippets("p1", "unmatched query")
        self.assertEqual(len(snippets), 1)
        self.assertIn("lexical_candidate_score", snippets[0])
        self.assertFalse(snippets[0]["semantic_support_verified"])

    def test_model_packets_separate_relevance_from_support(self) -> None:
        candidates = [{
            "passage_id": "P-001",
            "text": "The method did not improve accuracy.",
            "lexical_candidate_score": 1.0,
            "negation_warning": True,
        }]
        relevance = AcademicResearchSkill.build_relevance_review_packet(
            "Did the method improve accuracy?", candidates
        )
        support = AcademicResearchSkill.build_support_review_packet(
            "The method improved accuracy.", candidates
        )
        self.assertEqual(relevance["context_policy"], "fresh")
        self.assertIn("relevance", relevance["output_schema"]["items"][0])
        self.assertNotIn("verdict", relevance["output_schema"]["items"][0])
        self.assertEqual(support["context_policy"], "fresh")
        self.assertIn("verdict", support["output_schema"]["items"][0])
        self.assertNotIn("lexical_candidate_score", support["candidates"][0])

    def test_openalex_search_is_keyless_and_reconstructs_abstract(self) -> None:
        helper = AcademicResearchSkill(openalex_api_key=None)
        helper._get_json = Mock(return_value=({
            "results": [{
                "id": "https://openalex.org/W1",
                "doi": "https://doi.org/10.1000/example",
                "title": "Graph Retrieval",
                "publication_year": 2025,
                "cited_by_count": 4,
                "abstract_inverted_index": {"Graph": [0], "retrieval": [1], "works": [2]},
                "authorships": [{"author": {"display_name": "Ada Example"}}],
                "primary_location": {"source": {"display_name": "ExampleConf"}},
                "locations": [],
            }]
        }, helper._provider_status("openalex", "ok", False)))
        papers, status = helper.search_openalex_papers("graph retrieval")
        self.assertFalse(status["authenticated"])
        self.assertEqual(papers[0]["abstract"], "Graph retrieval works")
        self.assertEqual(papers[0]["externalIds"]["DOI"], "10.1000/example")

    def test_crossref_polite_pool_uses_optional_mailto(self) -> None:
        helper = AcademicResearchSkill(crossref_mailto="researcher@example.org")
        helper._get_json = Mock(return_value=({
            "message": {"items": [{
                "DOI": "10.1000/example",
                "title": ["Graph Retrieval"],
                "published-online": {"date-parts": [[2025, 1, 2]]},
                "is-referenced-by-count": 7,
                "author": [{"given": "Ada", "family": "Example"}],
                "container-title": ["Example Journal"],
                "URL": "https://doi.org/10.1000/example",
            }]}
        }, helper._provider_status("crossref", "ok", False)))
        papers, _ = helper.search_crossref_papers("graph retrieval")
        params = helper._get_json.call_args.args[2]
        self.assertEqual(params["mailto"], "researcher@example.org")
        self.assertEqual(papers[0]["year"], 2025)

    def test_external_search_deduplicates_and_preserves_partial_failure(self) -> None:
        helper = AcademicResearchSkill()
        arxiv = {
            "paperId": "arXiv:2501.00001",
            "title": "Graph Retrieval",
            "abstract": "A graph retrieval method.",
            "year": 2025,
            "citationCount": None,
            "influentialCitationCount": None,
            "externalIds": {"ArXiv": "2501.00001"},
            "source_provenance": ["arxiv"],
        }
        openalex = {
            "paperId": "W1",
            "title": "Graph Retrieval",
            "abstract": "",
            "year": 2025,
            "citationCount": 9,
            "influentialCitationCount": None,
            "externalIds": {
                "DOI": "10.1000/example",
                "ArXiv": "2501.00001",
                "OpenAlex": "https://openalex.org/W1",
            },
            "citation_metrics_status": "verified",
            "citation_metrics_source": "openalex",
            "source_provenance": ["openalex"],
        }
        helper.search_arxiv_with_status = Mock(return_value=(
            [arxiv], helper._provider_status("arxiv", "ok", False, 1)
        ))
        helper.search_openalex_papers = Mock(return_value=(
            [openalex], helper._provider_status("openalex", "ok", False, 1)
        ))
        helper.search_crossref_papers = Mock(return_value=(
            [], helper._provider_status("crossref", "rate_limited", False)
        ))
        result = helper.search_external_papers(
            "graph retrieval", providers=["arxiv", "openalex", "crossref"]
        )
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(
            result["papers"][0]["source_provenance"], ["arxiv", "openalex"]
        )
        self.assertEqual(result["papers"][0]["citationCount"], 9)
        self.assertFalse(result["complete"])
        self.assertIn("incomplete coverage", result["note"])

    def test_arxiv_rate_limit_is_not_reported_as_empty_success(self) -> None:
        helper = AcademicResearchSkill()
        response = Mock(status_code=429)
        with patch("paper_research_skill.requests.get", return_value=response):
            papers, status = helper.search_arxiv_with_status("graph retrieval")
        self.assertEqual(papers, [])
        self.assertEqual(status["status"], "rate_limited")


    def test_fulltext_retrieval_hashes_and_extracts_official_pdf(self) -> None:
        helper = AcademicResearchSkill()
        response = Mock(status_code=200, content=b"%PDF fixture", headers={"content-type": "application/pdf"}, url="https://arxiv.org/pdf/2501.00001")
        with TemporaryDirectory() as tmp, patch("paper_research_skill.requests.get", return_value=response), patch.object(
            helper, "_extract_pdf_text", return_value="Graph retrieval improves multi hop reasoning.\n\nA separate limitation paragraph."
        ):
            content = helper.retrieve_paper_content(
                {"paperId": "arXiv:2501.00001", "externalIds": {"ArXiv": "2501.00001"}}, tmp
            )
        self.assertEqual(content["access_level"], "full_text")
        self.assertEqual(len(content["sha256"]), 64)
        passages = helper.extract_evidence_passages("graph multi hop", content, max_passages=1, min_chars=10)
        self.assertEqual(passages[0]["access_level"], "full_text")
        self.assertEqual(passages[0]["source_sha256"], content["sha256"])

    def test_fulltext_failure_downgrades_without_promoting_metadata(self) -> None:
        helper = AcademicResearchSkill()
        response = Mock(status_code=404)
        with patch("paper_research_skill.requests.get", return_value=response):
            abstract = helper.retrieve_paper_content({"externalIds": {"ArXiv": "2501.00001"}, "abstract": "Verified abstract."})
            metadata = helper.retrieve_paper_content({"canonical_url": "https://doi.org/10.1000/example"})
        self.assertEqual(abstract["access_level"], "abstract_only")
        self.assertEqual(metadata["access_level"], "metadata_only")
        self.assertEqual(metadata["text"], "")


if __name__ == "__main__":
    unittest.main()
