"""Regression tests for the chapter-first paper orchestration pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paper_pipeline import (
    audit_chapter,
    command_audit_chapters,
    command_extract,
    command_prepare_chapters,
    command_prepare_synthesis,
    command_prepare_comprehension,
    command_prepare_local_comprehension,
    command_audit_comprehension,
    run_comprehension_pipeline,
    CodexModelRunner,
    extract_paper_sections,
    FINAL_REPORT_SECTIONS,
    read_json,
)
from report_auditor import REQUIRED_SECTIONS


class FakeComprehensionRunner:
    def __init__(self, unresolved: bool = False):
        self.unresolved = unresolved
        self.calls = []

    def run(self, packet, output_schema):
        self.calls.append(packet["job_id"])
        if packet["job_id"] == "COMPREHENSION-DISCOVER-FULL-REPORT":
            return {"report_hash": packet["report_hash"], "self_contained": False, "items": [{
                "id": "COMP-001", "claim_text": "Set ζ_align = 0.3.", "expression": "ζ_align",
                "reader_question": "What does ζ_align mean?", "first_use_text": "Set ζ_align = 0.3.",
                "first_use_heading": "Results", "issue": "undefined", "answered_in_report": False,
                "answer_text": None, "severity": "blocking"}]}
        if packet["job_id"] == "COMP-001-LOCAL-RESOLUTION":
            if self.unresolved:
                return {"id": "COMP-001", "status": "unresolved", "answer_from": "none",
                        "definition": None, "evidence_text": None, "passage_id": None,
                        "action": "mark_source_undefined", "reason": "source does not define it"}
            return {"id": "COMP-001", "status": "resolved", "answer_from": "source",
                    "definition": "alignment loss", "evidence_text": "ζ_align denotes alignment loss.",
                    "passage_id": "SEC-001-P001", "action": "rewrite", "reason": "definition is explicit"}
        if packet["job_id"] == "COMPREHENSION-REWRITE":
            return {"report": "ζ_align means alignment loss; set ζ_align = 0.3.",
                    "applied_issue_ids": ["COMP-001"]}
        if packet["job_id"] == "COMPREHENSION-FINAL-FULL-REPORT":
            return {"report_hash": packet["report_hash"], "self_contained": True, "items": []}
        raise AssertionError(packet["job_id"])


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

    def test_final_report_sections_match_auditor(self) -> None:
        expected = [
            "📌 一句話總結", "🧭 論文定位與研究問題", "📖 詳細原理解說",
            "🎨 ASCII 圖解", "📐 數學公式", "⚙️ 實作細節", "🧪 實驗設計與結果",
            "✅ 優缺點分析", "💡 適用場景建議", "限制、風險與未來方向", "📚 參考來源",
        ]
        self.assertEqual(FINAL_REPORT_SECTIONS, expected)
        self.assertEqual(REQUIRED_SECTIONS, expected)

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


    def test_comprehension_scan_is_open_world_and_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "final_report.md"
            report.write_text("## Results\n\nSet ζ_align = 0.3 without explaining it.", encoding="utf-8")
            result = command_prepare_comprehension(argparse.Namespace(workdir=root, report=report, stage="discover"))
            self.assertEqual(result, 0)
            packet = read_json(root / "comprehension/discover_full_report_scan_packet.json")
            self.assertEqual(packet["context_policy"], "fresh")
            self.assertIn("ζ_align", packet["report"])
            self.assertIn("do not use a predefined term list", " ".join(packet["instructions"]))

    def test_local_resolution_uses_only_report_and_source_passages(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "final_report.md"
            report.write_text("Set ζ_align = 0.3.", encoding="utf-8")
            command_prepare_comprehension(argparse.Namespace(workdir=root, report=report, stage="discover"))
            packet = read_json(root / "comprehension/discover_full_report_scan_packet.json")
            (root / "comprehension/discover_full_report_scan.json").write_text(json.dumps({
                "report_hash": packet["report_hash"], "self_contained": False,
                "items": [{"id": "COMP-001", "claim_text": "Set ζ_align = 0.3.", "expression": "ζ_align",
                           "reader_question": "What does ζ_align mean?", "first_use_text": "Set ζ_align = 0.3.",
                           "first_use_heading": "Results", "issue": "undefined", "answered_in_report": False,
                           "answer_text": None, "severity": "blocking"}],
            }), encoding="utf-8")
            (root / "evidence_index.jsonl").write_text(json.dumps({"passage_id": "SEC-001-P001", "section_title": "Method",
                "text": "ζ_align denotes alignment loss."}) + "\n", encoding="utf-8")
            self.assertEqual(command_prepare_local_comprehension(argparse.Namespace(workdir=root)), 0)
            local = read_json(root / "comprehension/local_packets/COMP-001.json")
            self.assertEqual(local["context_policy"], "fresh")
            self.assertEqual(local["candidate_source_passages"][0]["passage_id"], "SEC-001-P001")

    def test_comprehension_gate_rejects_stale_or_unresolved_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "final_report.md"
            report.write_text("Set X = 5.", encoding="utf-8")
            command_prepare_comprehension(argparse.Namespace(workdir=root, report=report, stage="final"))
            packet = read_json(root / "comprehension/final_full_report_scan_packet.json")
            scan = {"report_hash": packet["report_hash"], "self_contained": False,
                    "items": [{"id": "COMP-001", "severity": "blocking", "answered_in_report": False}]}
            (root / "comprehension/final_full_report_scan.json").write_text(json.dumps(scan), encoding="utf-8")
            self.assertEqual(command_audit_comprehension(argparse.Namespace(workdir=root, report=report)), 2)
            self.assertFalse(read_json(root / "comprehension/comprehension_gate.json")["passed"])
            report.write_text("X means the number of examples; X = 5.", encoding="utf-8")
            self.assertEqual(command_audit_comprehension(argparse.Namespace(workdir=root, report=report)), 2)
            self.assertIn("stale", " ".join(read_json(root / "comprehension/comprehension_gate.json")["reasons"]))


    def test_real_runner_contract_rewrites_then_rechecks_full_report(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "final_report.md"
            report.write_text("Set ζ_align = 0.3.", encoding="utf-8")
            (root / "evidence_index.jsonl").write_text(json.dumps({
                "passage_id": "SEC-001-P001", "section_title": "Method",
                "text": "ζ_align denotes alignment loss.",
            }) + "\n", encoding="utf-8")
            runner = FakeComprehensionRunner()
            gate = run_comprehension_pipeline(root, report, runner)
            self.assertTrue(gate["passed"])
            self.assertIn("ζ_align means alignment loss", report.read_text(encoding="utf-8"))
            self.assertTrue(report.with_suffix(".md.pre_comprehension").exists())
            self.assertEqual(runner.calls, [
                "COMPREHENSION-DISCOVER-FULL-REPORT", "COMP-001-LOCAL-RESOLUTION",
                "COMPREHENSION-REWRITE", "COMPREHENSION-FINAL-FULL-REPORT",
            ])

    def test_real_runner_contract_fails_closed_without_source_definition(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "final_report.md"
            original = "Set ζ_align = 0.3."
            report.write_text(original, encoding="utf-8")
            runner = FakeComprehensionRunner(unresolved=True)
            gate = run_comprehension_pipeline(root, report, runner)
            self.assertFalse(gate["passed"])
            self.assertEqual(report.read_text(encoding="utf-8"), original)
            self.assertNotIn("COMPREHENSION-REWRITE", runner.calls)


    def test_codex_runner_uses_global_options_before_exec(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fake_run(command, **kwargs):
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text("{}", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
            with patch("paper_pipeline.subprocess.run", side_effect=fake_run) as invoked:
                self.assertEqual(CodexModelRunner(root).run({"job_id": "TEST"}, schema), {})
            command = invoked.call_args.args[0]
            self.assertEqual(command[:4], ["codex", "--ask-for-approval", "never", "exec"])
            self.assertIn("--skip-git-repo-check", command)
            self.assertIn("--ephemeral", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")


if __name__ == "__main__":
    unittest.main()
