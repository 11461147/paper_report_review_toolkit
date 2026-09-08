"""Regression tests for the chapter-first paper orchestration pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paper_pipeline import (
    audit_chapter,
    command_audit_chapters,
    command_extract,
    command_prepare_chapters,
    command_prepare_synthesis,
    extract_paper_sections,
    read_json,
)


class SectionExtractionTests(unittest.TestCase):
    def test_numbered_and_unnumbered_sections(self) -> None:
        text = """Abstract
This abstract contains enough text to remain a complete section for testing.

1 Introduction
This introduction contains enough text to describe the research problem clearly.

2 Background
This background contains enough text to describe prior work and context.

2.1 Retrieval Systems
This subsection contains enough text to explain retrieval systems in detail.

3 Methods
This methods section contains enough text to explain the proposed workflow.

References
References remain in this final section and are not treated as report conclusions.
"""
        sections = extract_paper_sections(text)
        identities = [(item.number, item.title, item.level) for item in sections]
        self.assertIn(("", "Abstract", 1), identities)
        self.assertIn(("1", "Introduction", 1), identities)
        self.assertIn(("2.1", "Retrieval Systems", 2), identities)
        self.assertIn(("", "References", 1), identities)
        intro = next(item for item in sections if item.title == "Introduction")
        self.assertNotIn("2 Background", intro.text)

    def test_no_heading_fails_closed(self) -> None:
        self.assertEqual(extract_paper_sections("plain text without reliable structure"), [])

    def test_graphrag_fixture_detects_main_sections(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "tests/cases/graphrag/paper_extracted.txt"
        if not fixture.exists():
            self.skipTest("GraphRAG fixture is not present")
        sections = extract_paper_sections(fixture.read_text(encoding="utf-8"))
        titles = {item.title.casefold() for item in sections if item.level == 1}
        for title in ("introduction", "background", "methods", "analysis", "results", "discussion", "conclusion"):
            self.assertIn(title, titles)


class PipelineArtifactTests(unittest.TestCase):
    def _write_sections(self, root: Path) -> None:
        metadata = {"paper_id": "test:paper", "status": "complete"}
        sections = {
            **metadata,
            "sections": [
                {
                    "section_id": "SEC-001",
                    "number": "1",
                    "title": "Introduction",
                    "level": 1,
                    "text": "The method reports accuracy 76.8 on Dataset A and preserves scope.",
                    "start_line": 1,
                    "end_line": 2,
                    "page_start": 1,
                    "page_end": 1,
                    "heading_confidence": 0.94,
                    "detection_source": ["numbering"],
                },
                {
                    "section_id": "SEC-002",
                    "number": "2",
                    "title": "Results",
                    "level": 1,
                    "text": "The evaluation reports accuracy 80.1 on Dataset B under this setting.",
                    "start_line": 3,
                    "end_line": 4,
                    "page_start": 2,
                    "page_end": 2,
                    "heading_confidence": 0.94,
                    "detection_source": ["numbering"],
                },
            ],
        }
        (root / "paper_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (root / "sections.json").write_text(json.dumps(sections), encoding="utf-8")

    def test_fresh_context_packets_are_isolated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sections(root)
            result = command_prepare_chapters(argparse.Namespace(workdir=root))
            self.assertEqual(result, 0)
            jobs = read_json(root / "chapter_jobs.json")["jobs"]
            self.assertEqual(len(jobs), 2)
            packets = [read_json(root / job["packet"]) for job in jobs]
            self.assertTrue(all(packet["context_policy"] == "fresh" for packet in packets))
            self.assertNotIn("Dataset B", packets[0]["section"]["text"])
            self.assertNotIn("Dataset A", packets[1]["section"]["text"])

    def test_chapter_audit_checks_exact_evidence_and_numbers(self) -> None:
        section = {
            "section_id": "SEC-001",
            "text": "The method reports accuracy 76.8 on Dataset A and preserves scope.",
        }
        report = {
            "section_id": "SEC-001",
            "claims": [
                {
                    "claim_id": "SEC-001-CLM-001",
                    "text": "Accuracy is 76.8.",
                    "evidence_text": "The method reports accuracy 76.8 on Dataset A and preserves scope.",
                    "citation": "[Example et al., 2025, Section 1]",
                }
            ],
        }
        self.assertTrue(audit_chapter(report, section)["passed"])
        report["claims"][0]["text"] = "Accuracy is 78.6."
        self.assertFalse(audit_chapter(report, section)["passed"])

    def test_synthesis_is_blocked_until_all_chapters_pass(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_sections(root)
            (root / "chapter_audit_summary.json").write_text(
                json.dumps({"passed": False, "chapters": []}), encoding="utf-8"
            )
            (root / "verified_claim_registry.json").write_text(
                json.dumps({"claims": []}), encoding="utf-8"
            )
            result = command_prepare_synthesis(argparse.Namespace(workdir=root))
            self.assertEqual(result, 2)
            self.assertFalse(read_json(root / "synthesis_gate.json")["passed"])

    def test_extract_command_records_fail_closed_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "paper.txt"
            source.write_text("plain text without headings", encoding="utf-8")
            workdir = root / "out"
            result = command_extract(
                argparse.Namespace(source=source, workdir=workdir, paper_id=None)
            )
            self.assertEqual(result, 2)
            self.assertEqual(
                read_json(workdir / "paper_metadata.json")["status"],
                "section_extraction_failed",
            )


if __name__ == "__main__":
    unittest.main()
