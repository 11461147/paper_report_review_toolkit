"""Smoke tests for report_auditor hard gates.

Run:
  python tools/smoke_test_auditor.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_auditor import (
    apply_final_review,
    audit_report,
    build_final_review,
    build_reasoning_appendix_template,
    build_argument_packet,
    build_reasoning_packet,
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
    return "\n\n".join(f"## {section}\n\n{body[section]}" for section in sections)


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


def main() -> int:
    test_empty_report_fails()
    test_uncited_report_fails()
    test_complete_report_passes_hard_gates()
    test_reasoning_outputs_include_appendix_contract()
    test_required_model_reviews_block_when_missing()
    test_reasoning_verdict_blocks_final_pass()
    print("smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
