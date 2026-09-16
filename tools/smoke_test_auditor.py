"""Smoke tests for report_auditor hard gates.

Run:
  python tools/smoke_test_auditor.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_auditor import (
    MIN_REPORT_CHARS,
    apply_final_review,
    audit_report,
    build_final_review,
    build_reasoning_appendix_template,
    build_argument_packet,
    build_reasoning_packet,
    build_semantic_packet,
    load_external_evidence_registry,
    read_source_text,
)


def assert_failed_by_gate(report: str, expected_gate: str) -> None:
    result = audit_report(report, verify_citation_metadata_enabled=False)
    failures = result["hard_gates"]["failures"]
    gates = {failure["gate"] for failure in failures}
    assert not result["passed"], f"expected report to fail, got pass: {result}"
    assert expected_gate in gates, f"expected gate {expected_gate}, got {gates}"


def complete_report() -> str:
    sections = [
        "📌 一句話總結",
        "🧭 論文定位與研究問題",
        "📖 詳細原理解說",
        "🎨 ASCII 圖解",
        "📐 數學公式",
        "⚙️ 實作細節",
        "🧪 實驗設計與結果",
        "✅ 優缺點分析",
        "💡 適用場景建議",
        "📚 參考來源",
    ]
    body = {
        sections[0]: "本報告只在原文提供的任務與資料集範圍內總結方法定位。[Lee et al., 2024, Section 1]",
        sections[1]: "研究問題是如何在指定任務中降低錯誤率，報告不延伸到其他任務。[Lee et al., 2024, Section 2]",
        sections[2]: "方法流程包含資料前處理、模型訓練與評估三個步驟，皆依原文描述整理。[Lee et al., 2024, Section 3]",
        sections[3]: "流程圖以文字呈現：input -> encoder -> scorer -> output，僅作讀者理解輔助。[Lee et al., 2024, Figure 1]",
        sections[4]: "公式說明保留原文變數名稱，避免加入報告作者自己的未驗證推導。[Lee et al., 2024, Section 3]",
        sections[5]: "實作細節包含模型設定與資料處理順序，但不宣稱能完全重現所有結果。[Lee et al., 2024, Section 4]",
        sections[6]: "實驗結果只描述原文表格中的比較方向，沒有把單一資料集結果泛化到所有場景。[Lee et al., 2024, Table 2]",
        sections[7]: "優點是流程清楚且評估設定明確；限制是報告只能依原文資料討論。[Lee et al., 2024, Section 5]",
        sections[8]: "適用場景建議限定在與原文任務相近的資料條件下，不作跨領域保證。[Lee et al., 2024, Section 6]",
        sections[9]: "- Lee et al. (2024). Example Paper. https://doi.org/10.1000/example",
    }
    report = "\n\n".join(f"## {section}\n\n{body[section]}" for section in sections)
    padding = (
        "補充說明維持原文的資料集、方法、評估指標與限制條件，不加入來源未支持的泛化結論。"
        "[Lee et al., 2024, Section 6]"
    )
    while len(report) < MIN_REPORT_CHARS + 100:
        report += "\n\n" + padding
    return report


def test_empty_report_fails() -> None:
    assert_failed_by_gate("", "minimum_report_length")


def test_uncited_report_fails() -> None:
    report = "這是一份沒有 citation 的短報告。它提出幾個看似合理的說法，但沒有參考來源。"
    assert_failed_by_gate(report, "minimum_cited_claims")


def test_complete_report_passes_hard_gates() -> None:
    result = audit_report(complete_report(), verify_citation_metadata_enabled=False)
    assert result["hard_gates"]["passed"], result["hard_gates"]



def test_argument_packet_contains_normalization_schema() -> None:
    result = audit_report(complete_report(), verify_citation_metadata_enabled=False)
    packet = build_argument_packet(result)
    assert packet["packet_type"] == "argument_normalization_l3a"
    assert packet["items"], packet
    schema = packet["items"][0]["model_output_schema"]["normalized_argument"]
    assert "premises" in schema
    assert "conclusion" in schema
    assert "scope_conditions" in schema
    assert "inference_rule" in schema


def test_pdf_source_is_converted_to_text() -> None:
    with TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test fixture")
        with patch("report_auditor.shutil.which", return_value="/usr/bin/pdftotext"), patch(
            "report_auditor.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "論文原文 76.8%".encode("utf-8")
            run.return_value.stderr = b""
            text = read_source_text(pdf_path)

        assert text == "論文原文 76.8%"
        run.assert_called_once_with(
            ["/usr/bin/pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True,
            check=False,
        )


def test_reasoning_outputs_include_appendix_contract() -> None:
    result = audit_report(complete_report(), verify_citation_metadata_enabled=False)
    packet = build_reasoning_packet(result)
    appendix = build_reasoning_appendix_template(result)
    assert packet["appendix_output_contract"]["section_title"] == "推論有效性審查附錄"
    assert "## 推論有效性審查附錄" in appendix
    assert "L3-001" in appendix


def test_required_model_reviews_block_when_missing() -> None:
    result = audit_report(complete_report(), verify_citation_metadata_enabled=False)
    final_review = build_final_review(result, require_model_reviews=True)
    apply_final_review(result, final_review)
    assert not result["passed"], final_review
    layers = {issue["layer"] for issue in final_review["blocking_issues"]}
    assert layers == {"L2", "L3"}, layers


def test_reasoning_verdict_blocks_final_pass() -> None:
    result = audit_report(complete_report(), verify_citation_metadata_enabled=False)
    assert result["passed"], result
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        l2_path = tmp_path / "semantic.json"
        l3_path = tmp_path / "reasoning.json"
        l2_path.write_text(
            json.dumps({"items": [{"id": "L2-001", "evidence_status": "supported", "inference_risk": "low", "action": "keep"}]}),
            encoding="utf-8",
        )
        l3_path.write_text(
            json.dumps({"items": [{"id": "L3-001", "verdict": "overgeneralized", "reason": "scope widened"}]}),
            encoding="utf-8",
        )
        final_review = build_final_review(result, semantic_verdicts=l2_path, reasoning_verdicts=l3_path)
    apply_final_review(result, final_review)
    assert not result["passed"], final_review
    assert final_review["reasoning_review"]["counts"]["blocking"] == 1


def test_external_passages_are_attached_to_matching_l2_items() -> None:
    result = {
        "passed": True, "final_score": 90, "threshold": 80, "scores": {}, "counts": {},
        "deterministic_checks": {},
        "rewrite_contexts": [{"category": "citation_precision", "severity": "major",
                              "message": "check external claim", "context": "Claim text（EXT-SRC-007）"}],
        "external_evidence_registry": {"sources": [{
            "source_id": "EXT-SRC-007",
            "paper": {"title": "Example", "year": 2025},
            "evidence_passages": [{"passage_id": "EXT-SRC-007-P01", "text": "The experiment reports a scoped result.",
                                   "access_level": "full_text", "source_url": "https://arxiv.org/abs/2501.00001",
                                   "source_sha256": "abc", "location": {"section": "Results"}}],
        }]},
    }
    packet = build_semantic_packet(result)
    attached = packet["items"][0]["external_evidence"]
    assert attached[0]["source_id"] == "EXT-SRC-007"
    assert attached[0]["evidence_passages"][0]["access_level"] == "full_text"


def test_external_source_reuse_creates_distinct_claim_reviews() -> None:
    result = {
        "passed": True, "final_score": 90, "threshold": 80, "scores": {}, "counts": {},
        "deterministic_checks": {}, "rewrite_contexts": [],
        "report_text": "方法處理全域問題。（EXT-SRC-001）\n\n方法降低延遲。（EXT-SRC-001）",
        "external_evidence_registry": {"sources": [{
            "source_id": "EXT-SRC-001",
            "evidence_passages": [{
                "passage_id": "EXT-SRC-001-P01", "text": "Evidence.",
                "access_level": "full_text", "source_url": "https://arxiv.org/abs/2501.00001",
            }],
        }]},
    }
    items = build_semantic_packet(result)["items"]
    assert [item["id"] for item in items] == ["L2-EXT-001", "L2-EXT-002"]
    assert all(item["reviewed_source_id"] == "EXT-SRC-001" for item in items)
    assert items[0]["report_claim"] != items[1]["report_claim"]


def test_undefined_external_source_is_fail_closed() -> None:
    result = {
        "passed": True, "final_score": 90, "threshold": 80, "scores": {}, "counts": {},
        "deterministic_checks": {}, "rewrite_contexts": [],
        "report_text": "此結論引用不存在的來源。（EXT-SRC-999）",
        "external_evidence_registry": {"sources": []},
    }
    item = build_semantic_packet(result)["items"][0]
    assert item["registry_precheck"] == "undefined_source"
    review = build_final_review(result)
    assert not review["passed"]
    assert any(issue["category"] == "undefined_source" for issue in review["blocking_issues"])


def test_omitted_external_review_id_is_blocking() -> None:
    result = {
        "passed": True, "final_score": 90, "threshold": 80, "scores": {}, "counts": {},
        "deterministic_checks": {}, "rewrite_contexts": [],
        "report_text": "外部結果支持此結論。（EXT-SRC-001）",
        "external_evidence_registry": {"sources": [{
            "source_id": "EXT-SRC-001",
            "evidence_passages": [{
                "passage_id": "EXT-SRC-001-P01", "text": "Scoped result evidence.",
                "access_level": "full_text", "source_url": "https://arxiv.org/abs/2501.00001",
            }],
        }]},
    }
    with TemporaryDirectory() as tmp:
        verdicts = Path(tmp) / "semantic.json"
        verdicts.write_text(json.dumps({"verdicts": []}), encoding="utf-8")
        review = build_final_review(result, semantic_verdicts=verdicts)
    assert not review["passed"]
    assert any(item["category"] == "missing_semantic_item"
               for item in review["semantic_review"]["blocking_items"])


def test_metadata_only_cannot_contain_content_evidence() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "external.json"
        path.write_text(json.dumps({"sources": [{"source_id": "EXT-SRC-001", "evidence_passages": [
            {"passage_id": "EXT-SRC-001-P01", "access_level": "metadata_only", "text": "invented result"}
        ]}]}), encoding="utf-8")
        try:
            load_external_evidence_registry(path)
        except ValueError:
            pass
        else:
            raise AssertionError("metadata_only content evidence must be rejected")


def main() -> int:
    test_empty_report_fails()
    test_uncited_report_fails()
    test_complete_report_passes_hard_gates()
    test_argument_packet_contains_normalization_schema()
    test_pdf_source_is_converted_to_text()
    test_reasoning_outputs_include_appendix_contract()
    test_required_model_reviews_block_when_missing()
    test_reasoning_verdict_blocks_final_pass()
    test_external_passages_are_attached_to_matching_l2_items()
    test_external_source_reuse_creates_distinct_claim_reviews()
    test_undefined_external_source_is_fail_closed()
    test_omitted_external_review_id_is_blocking()
    test_metadata_only_cannot_contain_content_evidence()
    print("smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
