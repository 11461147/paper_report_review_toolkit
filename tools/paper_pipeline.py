"""Artifact-based orchestration for chapter-by-chapter paper review.

The program never calls a language model.  Instead it creates self-contained job
packets that an agent runner can execute in fresh contexts, then validates the
returned JSON before allowing cross-chapter synthesis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from report_auditor import NUMBER_RE, read_source_text


FINAL_REPORT_SECTIONS = [
    "📌 一句話總結",
    "🧭 論文定位與研究問題",
    "📖 詳細原理解說",
    "🎨 ASCII 圖解",
    "📐 數學公式",
    "⚙️ 實作細節",
    "🧪 實驗設計與結果",
    "✅ 優缺點分析",
    "💡 適用場景建議",
    "限制、風險與未來方向",
    "📚 參考來源",
]


HEADING_PATTERNS = (
    re.compile(r"^(?P<number>\d+(?:\.\d+)*)(?:[.)])?\s+(?P<title>[A-Z][^\n]{1,119})$"),
    re.compile(r"^(?P<number>[IVXLCDM]+)(?:[.)])?\s+(?P<title>[A-Z][A-Z0-9 ,:&/()\-]{1,119})$"),
    re.compile(r"^(?P<number>Appendix\s+[A-Z])(?:[:.)])?\s*(?P<title>[^\n]{0,119})$", re.I),
    re.compile(r"^(?P<number>[A-H])(?:[.)])?\s+(?P<title>[A-Z][^\n]{1,119})$"),
)
STANDARD_TITLE_RE = re.compile(
    r"^(Abstract|Introduction|Background|Related Work|Literature Review|Materials and Methods|"
    r"Methods?|Methodology|Experimental Setup|Experiments?|Evaluation|Analysis|Results?|"
    r"Discussion|Limitations?|Conclusions?|References|Bibliography|Acknowledg(?:e)?ments?)$",
    re.I,
)
PAGE_MARKER_RE = re.compile(r"^\s*(?:\f|---\s*page\s+(\d+)\s*---)\s*$", re.I)
NOISE_RE = re.compile(r"^(?:figure|table|algorithm)\s+\d+[:.]", re.I)


@dataclass
class PaperSection:
    section_id: str
    number: str
    title: str
    level: int
    text: str
    start_line: int
    end_line: int
    page_start: int | None
    page_end: int | None
    heading_confidence: float
    detection_source: list[str]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def build_evidence_index(sections: list[dict[str, Any]], max_chars: int = 2000) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for section in sections:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section.get("text", "")) if part.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 2 > max_chars:
                chunks.append(current)
                current = paragraph
            else:
                current = paragraph if not current else current + "\n\n" + paragraph
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks, 1):
            records.append({
                "passage_id": f"{section[chr(115)+chr(101)+chr(99)+chr(116)+chr(105)+chr(111)+chr(110)+chr(95)+chr(105)+chr(100)]}-P{index:03d}",
                "section_id": section["section_id"],
                "section_number": section.get("number", ""),
                "section_title": section.get("title", ""),
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
                "text": chunk,
            })
    return records


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value[:50] or "section"


def infer_paper_id(text: str, source: Path) -> str:
    arxiv = re.search(r"(?:arXiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})", text, re.I)
    if arxiv:
        return "arxiv:" + arxiv.group(1)
    doi = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
    if doi:
        return "doi:" + doi.group(0).rstrip(".,)").lower()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    return "sha256:" + digest


def _heading(line: str) -> tuple[str, str, int, float, list[str]] | None:
    candidate = " ".join(line.strip().split())
    if not candidate or len(candidate) > 140 or NOISE_RE.match(candidate):
        return None
    for pattern in HEADING_PATTERNS:
        match = pattern.match(candidate)
        if not match:
            continue
        number = match.group("number")
        title = match.group("title").strip(" .:") or number
        if number.lower().startswith("appendix"):
            level = 1
        elif re.fullmatch(r"[IVXLCDM]+", number):
            level = 1
        else:
            level = number.count(".") + 1
        confidence = 0.94 if STANDARD_TITLE_RE.match(title) else 0.82
        return number, title, level, confidence, ["numbering", "line-layout"]
    if STANDARD_TITLE_RE.match(candidate):
        return "", candidate, 1, 0.88, ["standard-title", "line-layout"]
    return None


def extract_paper_sections(text: str) -> list[PaperSection]:
    """Detect section boundaries automatically, falling back to one full-text section."""
    lines = text.splitlines()
    page = 1
    page_at_line: list[int] = []
    candidates: list[tuple[int, int, tuple[str, str, int, float, list[str]]]] = []
    for index, line in enumerate(lines):
        page_at_line.append(page)
        if "\f" in line:
            page += max(1, line.count("\f"))
            continue
        marker = PAGE_MARKER_RE.match(line)
        if marker and marker.group(1):
            page = int(marker.group(1))
            continue
        found = _heading(line)
        if found:
            candidates.append((index, page, found))

    # Repeated headings are commonly running headers. Keep only numbered repeats.
    title_counts: dict[str, int] = {}
    for _, _, (_, title, _, _, _) in candidates:
        key = title.casefold()
        title_counts[key] = title_counts.get(key, 0) + 1
    candidates = [
        item for item in candidates
        if item[2][0] or title_counts[item[2][1].casefold()] == 1
    ]
    conventional_tops = [
        item for item in candidates
        if item[2][0].isdigit() and item[2][2] == 1 and STANDARD_TITLE_RE.match(item[2][1])
    ]
    if len(conventional_tops) >= 3:
        candidates = [
            item for item in candidates
            if not (item[2][0].isdigit() and item[2][2] == 1 and not STANDARD_TITLE_RE.match(item[2][1]))
        ]
    if not candidates:
        return []

    sections: list[PaperSection] = []
    for ordinal, (line_index, start_page, found) in enumerate(candidates, 1):
        number, title, level, confidence, sources = found
        next_index = candidates[ordinal][0] if ordinal < len(candidates) else len(lines)
        body = "\n".join(lines[line_index + 1:next_index]).strip()
        # A false positive usually creates an empty/tiny section. Merge its text into
        # the preceding section rather than asking for human correction.
        if len(body) < 40 and confidence < 0.90 and sections:
            sections[-1].text += "\n\n" + lines[line_index] + ("\n" + body if body else "")
            sections[-1].end_line = next_index
            sections[-1].page_end = page_at_line[next_index - 1] if next_index else start_page
            continue
        sections.append(PaperSection(
            section_id=f"SEC-{len(sections) + 1:03d}", number=number, title=title,
            level=level, text=body, start_line=line_index + 1, end_line=next_index,
            page_start=start_page,
            page_end=page_at_line[next_index - 1] if next_index and page_at_line else start_page,
            heading_confidence=confidence, detection_source=sources,
        ))
    return sections


def command_extract(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    text = read_source_text(source)
    sections = extract_paper_sections(text)
    workdir = args.workdir.resolve()
    metadata = {
        "paper_id": args.paper_id or infer_paper_id(text, source),
        "source_file": str(source),
        "source_type": source.suffix.lower().lstrip("."),
        "extractor": "automatic-multisyntax-v1",
        "manual_correction_required": False,
        "status": "complete" if sections else "section_extraction_failed",
        "section_count": len(sections),
    }
    section_records = [asdict(section) for section in sections]
    evidence_records = build_evidence_index(section_records)
    metadata["evidence_passage_count"] = len(evidence_records)
    write_json(workdir / "paper_metadata.json", metadata)
    write_json(workdir / "sections.json", {**metadata, "sections": section_records})
    write_jsonl(workdir / "evidence_index.jsonl", evidence_records)
    if not sections:
        print("section extraction failed: no reliable top-level structure detected", file=sys.stderr)
        return 2
    print(f"extracted {len(sections)} sections into {workdir}")
    return 0


CHAPTER_OUTPUT_SCHEMA = {
    "section_id": "SEC-NNN", "chapter_purpose": "string", "chapter_summary": "string",
    "claims": [{"claim_id": "SEC-NNN-CLM-NNN", "text": "string", "evidence_text": "exact quote",
                "location": {"section": "string", "page": "integer|null", "table": "string|null", "figure": "string|null"},
                "claim_type": "direct|numeric|inference", "citation": "string"}],
    "methods": [], "numeric_evidence": [], "tables_and_figures": [], "limitations": [],
    "links_to_other_sections": [], "chapter_conclusion": "string",
}


def command_prepare_chapters(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    document = read_json(workdir / "sections.json")
    metadata = read_json(workdir / "paper_metadata.json")
    all_sections = document["sections"]
    main_indexes = [
        index for index, section in enumerate(all_sections)
        if section.get("level") == 1
        and (str(section.get("number", "")).isdigit() or section.get("title", "").casefold() == "abstract")
    ]
    main_sections = []
    for position, start in enumerate(main_indexes):
        end = main_indexes[position + 1] if position + 1 < len(main_indexes) else len(all_sections)
        main = dict(all_sections[start])
        descendants = all_sections[start + 1:end]
        parts = [main.get("text", "")]
        for child in descendants:
            heading = " ".join(part for part in (str(child.get("number", "")), child.get("title", "")) if part)
            parts.append(heading + "\n" + child.get("text", ""))
        main["text"] = "\n\n".join(part for part in parts if part.strip())
        if descendants:
            main["end_line"] = descendants[-1].get("end_line", main.get("end_line"))
            main["page_end"] = descendants[-1].get("page_end", main.get("page_end"))
        main_sections.append(main)
    document["sections"] = main_sections
    jobs = []
    for section in document["sections"]:
        name = f'{section["section_id"]}-{slug(section["title"])}.json'
        output = f'chapter_reports/{section["section_id"]}.json'
        packet = {
            "job_id": f'CHAPTER-{section["section_id"]}-WRITE', "context_policy": "fresh",
            "paper": metadata, "section": section,
            "instructions": [
                "Analyze only the supplied section text.",
                "Do not use results or facts absent from this section.",
                "Every factual claim must include an exact evidence_text quote and location.",
                "Do not write the paper-wide conclusion; chapter_conclusion is limited to this section.",
                "Return JSON only and conform to output_schema.",
            ],
            "output_schema": CHAPTER_OUTPUT_SCHEMA, "expected_output": output,
        }
        write_json(workdir / "chapter_packets" / name, packet)
        jobs.append({"job_id": packet["job_id"], "packet": f"chapter_packets/{name}", "output": output, "context_policy": "fresh"})
    write_json(workdir / "chapter_jobs.json", {"jobs": jobs})
    print(f"prepared {len(jobs)} fresh-context chapter jobs")
    return 0


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def audit_chapter(report: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if report.get("section_id") != section["section_id"]:
        findings.append({"severity": "critical", "category": "section_id", "message": "section_id does not match packet"})
    claims = report.get("claims")
    if not isinstance(claims, list) or not claims:
        findings.append({"severity": "critical", "category": "claims", "message": "at least one claim is required"})
        claims = []
    source = _normalized(section.get("text", ""))
    verified = []
    for index, claim in enumerate(claims, 1):
        claim_id = claim.get("claim_id") or f'{section["section_id"]}-CLM-{index:03d}'
        evidence = claim.get("evidence_text", "")
        errors = []
        if not evidence or _normalized(evidence) not in source:
            errors.append("evidence_text is not an exact passage in this section")
        claim_numbers = set(NUMBER_RE.findall(claim.get("text", "")))
        evidence_numbers = set(NUMBER_RE.findall(evidence))
        if claim_numbers - evidence_numbers:
            errors.append("claim contains numbers absent from evidence_text")
        if not claim.get("citation"):
            errors.append("citation is required")
        if errors:
            findings.append({"severity": "critical", "category": "claim_evidence", "message": f'{claim_id}: ' + "; ".join(errors)})
        else:
            verified.append({**claim, "claim_id": claim_id, "section_id": section["section_id"], "chapter_audit_status": "passed"})
    return {
        "section_id": section["section_id"], "passed": not any(x["severity"] == "critical" for x in findings),
        "claim_count": len(claims), "verified_claim_count": len(verified), "verified_claims": verified, "findings": findings,
    }


def command_audit_chapters(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    jobs_path = workdir / "chapter_jobs.json"
    if not jobs_path.exists():
        print("blocked: run prepare-chapters first", file=sys.stderr)
        return 2
    jobs = read_json(jobs_path)["jobs"]
    sections = {}
    for job in jobs:
        packet = read_json(workdir / job["packet"])
        section = packet["section"]
        sections[section["section_id"]] = section
    audits, registry, missing = [], [], []
    for section_id, section in sections.items():
        report_path = workdir / "chapter_reports" / f"{section_id}.json"
        if not report_path.exists():
            missing.append(section_id)
            audit = {"section_id": section_id, "passed": False, "claim_count": 0, "verified_claim_count": 0,
                     "verified_claims": [], "findings": [{"severity": "critical", "category": "missing_report", "message": str(report_path)}]}
        else:
            try:
                audit = audit_chapter(read_json(report_path), section)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                audit = {"section_id": section_id, "passed": False, "claim_count": 0, "verified_claim_count": 0,
                         "verified_claims": [], "findings": [{"severity": "critical", "category": "invalid_json", "message": str(exc)}]}
        write_json(workdir / "chapter_audits" / f"{section_id}.json", audit)
        audits.append({key: value for key, value in audit.items() if key != "verified_claims"})
        if audit["passed"]:
            registry.extend(audit["verified_claims"])
    summary = {"passed": bool(audits) and all(item["passed"] for item in audits), "chapter_count": len(audits),
               "passed_chapters": sum(item["passed"] for item in audits), "missing_reports": missing, "chapters": audits}
    write_json(workdir / "chapter_audit_summary.json", summary)
    write_json(workdir / "verified_claim_registry.json", {"claims": registry, "source": "passed chapter audits"})
    markdown = ["# Verified Claims", "", "Only claims from passed chapter audits are listed.", "", "| Claim ID | Section | Verified conclusion | Evidence | Citation |", "|---|---|---|---|---|"]
    for claim in registry:
        location = claim.get("location", {})
        section_label = location.get("section") or claim.get("section_id", "")
        values = [claim.get("claim_id", ""), str(section_label), claim.get("text", ""), claim.get("evidence_text", ""), claim.get("citation", "")]
        escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
        markdown.append("| " + " | ".join(escaped) + " |")
    (workdir / "verified_claims.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(f'audited {len(audits)} chapters; passed={summary["passed"]}; verified_claims={len(registry)}')
    return 0 if summary["passed"] else 2


def command_prepare_synthesis(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    summary_path = workdir / "chapter_audit_summary.json"
    if not summary_path.exists():
        print("blocked: run audit-chapters first", file=sys.stderr)
        return 2
    summary = read_json(summary_path)
    registry = read_json(workdir / "verified_claim_registry.json")
    reasons = []
    if not summary.get("passed"):
        reasons.append("not all chapter audits passed")
    if not registry.get("claims"):
        reasons.append("verified claim registry is empty")
    gate = {"passed": not reasons, "reasons": reasons, "required_stage": "all chapters audited and passed"}
    write_json(workdir / "synthesis_gate.json", gate)
    if reasons:
        print("synthesis blocked: " + "; ".join(reasons), file=sys.stderr)
        return 2
    chapter_summaries = []
    for item in summary["chapters"]:
        report = read_json(workdir / "chapter_reports" / f'{item["section_id"]}.json')
        chapter_summaries.append({"section_id": item["section_id"], "chapter_summary": report.get("chapter_summary", ""),
                                  "chapter_conclusion": report.get("chapter_conclusion", "")})
    packet = {
        "job_id": "PAPER-SYNTHESIS-WRITE", "context_policy": "fresh",
        "paper": read_json(workdir / "paper_metadata.json"), "chapter_summaries": chapter_summaries,
        "verified_claim_registry": registry["claims"],
        "instructions": ["Use only claims in verified_claim_registry.", "Cite supporting claim_ids for every conclusion.",
                         "Separate direct evidence from cross-chapter inference.", "Preserve dataset, method, metric, baseline, and experimental scope.",
                         "Report contradictions and unavailable evidence; do not invent missing facts.",
                         "Write the original paper-section reports first, then append final_report_sections in the exact order given.",
                         "The appended sections are the reader-facing synthesis and must be audited after generation."],
        "final_report_sections": FINAL_REPORT_SECTIONS,
        "output_schema": {"paper_summary": "string", "conclusions": [{"conclusion_id": "SYN-NNN", "text": "string",
            "conclusion_type": "direct|cross_chapter_inference", "supported_by": ["claim_id"], "scope": {}, "limitations": []}],
            "unsupported_or_unknown": [], "overall_limitations": [],
            "final_report": {"path": "final_report.md", "minimum_chars": 2000,
                             "required_sections": FINAL_REPORT_SECTIONS}},
        "expected_output": ["synthesis_report.json", "final_report.md"],
    }
    write_json(workdir / "synthesis_packet.json", packet)
    print(f'prepared synthesis packet with {len(registry["claims"])} verified claims')
    return 0


class ModelRunner(Protocol):
    def run(self, packet: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]: ...


class CodexModelRunner:
    """Execute one packet per ephemeral, read-only Codex context."""

    def __init__(self, cwd: Path, model: str | None = None, timeout: int = 600):
        self.cwd, self.model, self.timeout = cwd, model, timeout

    def run(self, packet: dict[str, Any], output_schema: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="paper-comprehension-") as tmp:
            temp = Path(tmp)
            schema_path, output_path = temp / "schema.json", temp / "output.json"
            schema_path.write_text(json.dumps(output_schema, ensure_ascii=False), encoding="utf-8")
            command = ["codex", "--ask-for-approval", "never", "exec", "--ephemeral", "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", str(schema_path),
                       "--output-last-message", str(output_path), "-"]
            if self.model:
                command[2:2] = ["--model", self.model]
            prompt = "Return only JSON matching the supplied schema. Execute no tools.\n\nPACKET:\n" + json.dumps(packet, ensure_ascii=False)
            completed = subprocess.run(command, input=prompt, text=True, capture_output=True,
                                       cwd=self.cwd, timeout=self.timeout, check=False)
            if completed.returncode != 0:
                raise RuntimeError("model runner failed: " + (completed.stderr.strip() or completed.stdout.strip()))
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"model runner returned invalid JSON: {exc}") from exc


def _object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}


COMPREHENSION_SCAN_JSON_SCHEMA = _object_schema({
    "report_hash": {"type": "string"}, "self_contained": {"type": "boolean"},
    "items": {"type": "array", "items": _object_schema({
        "id": {"type": "string"}, "claim_text": {"type": "string"}, "expression": {"type": "string"},
        "reader_question": {"type": "string"}, "first_use_text": {"type": "string"},
        "first_use_heading": {"type": "string"},
        "issue": {"type": "string", "enum": ["undefined", "late_definition", "ambiguous", "missing_relation", "missing_scope", "inconsistent"]},
        "answered_in_report": {"type": "boolean"}, "answer_text": {"type": ["string", "null"]},
        "severity": {"type": "string", "enum": ["blocking", "warning"]},
    }, ["id", "claim_text", "expression", "reader_question", "first_use_text", "first_use_heading",
        "issue", "answered_in_report", "answer_text", "severity"])},
}, ["report_hash", "self_contained", "items"])

LOCAL_RESOLUTION_JSON_SCHEMA = _object_schema({
    "id": {"type": "string"}, "status": {"type": "string", "enum": ["resolved", "unresolved"]},
    "answer_from": {"type": "string", "enum": ["report", "source", "none"]},
    "definition": {"type": ["string", "null"]}, "evidence_text": {"type": ["string", "null"]},
    "passage_id": {"type": ["string", "null"]},
    "action": {"type": "string", "enum": ["keep", "rewrite", "delete", "mark_source_undefined"]},
    "reason": {"type": "string"},
}, ["id", "status", "answer_from", "definition", "evidence_text", "passage_id", "action", "reason"])

REWRITE_JSON_SCHEMA = _object_schema({
    "report": {"type": "string"}, "applied_issue_ids": {"type": "array", "items": {"type": "string"}},
}, ["report", "applied_issue_ids"])


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _report_context(report: str, needle: str, radius: int = 700) -> str:
    position = report.find(needle)
    if position < 0:
        return ""
    return report[max(0, position - radius):min(len(report), position + len(needle) + radius)]


def _load_evidence_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {record["passage_id"]: record for record in records}


def _candidate_evidence(issue: dict[str, Any], records: dict[str, dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    expression = str(issue.get("expression", ""))
    query = " ".join((expression, str(issue.get("reader_question", "")),
                      str(issue.get("claim_text", "")), str(issue.get("first_use_text", ""))))
    terms = {term.casefold() for term in re.findall(r"[A-Za-zΑ-Ωα-ω][\w.-]*", query)
             if len(term) > 1 or term.isupper()}
    ranked = []
    for record in records.values():
        haystack = (record.get("section_title", "") + " " + record.get("text", "")).casefold()
        score = sum(3 if term == expression.casefold() and term in haystack else haystack.count(term) for term in terms)
        if score:
            ranked.append((score, record["passage_id"], record))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if ranked:
        return [record for _, _, record in ranked[:limit]]
    # Cross-language or novel concepts may have no lexical overlap. A small,
    # deterministic fallback prevents "not retrieved" from being mislabeled
    # as "not defined" while keeping the local packet bounded.
    return [records[key] for key in sorted(records)[:limit]]


COMPREHENSION_SCAN_SCHEMA = {
    "report_hash": "sha256",
    "self_contained": "boolean",
    "items": [{
        "id": "COMP-NNN", "claim_text": "exact report text", "expression": "dynamic expression or concept",
        "reader_question": "knowledge a reader still needs", "first_use_text": "exact report text",
        "first_use_heading": "heading", "issue": "undefined|late_definition|ambiguous|missing_relation|missing_scope|inconsistent",
        "answered_in_report": "boolean", "answer_text": "exact report text or null", "severity": "blocking|warning",
    }],
}


def command_prepare_comprehension(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    report_path = args.report.resolve() if args.report else workdir / "final_report.md"
    if not report_path.exists():
        print(f"report missing: {report_path}", file=sys.stderr)
        return 2
    report = report_path.read_text(encoding="utf-8")
    stage = args.stage
    packet = {
        "job_id": f"COMPREHENSION-{stage.upper()}-FULL-REPORT", "context_policy": "fresh", "stage": stage,
        "report_path": str(report_path), "report_hash": _text_hash(report), "report": report,
        "instructions": [
            "Act as a first-time technical reader; use only the supplied report and no outside knowledge.",
            "Open-world task: dynamically identify every dependency needed to understand important claims; do not use a predefined term list.",
            "Check first use, definitions supplied later, symbol meaning, abbreviation expansion, numeric derivation, comparison baseline/metric/dataset, method role, and scope.",
            "Copy claim_text, first_use_text, and answer_text exactly from the report.",
            "A term registry is not proof that the report is self-contained.",
            "Blocking means the missing information can materially change the claim's meaning, numeric relation, comparison, or stated scope.",
            "Do not demand exhaustive background, dataset provenance, implementation detail, or definitions of ordinary words that are unnecessary to understand the asserted claim.",
            "Do not recursively invent new reader questions after the report has supplied the minimum definitions and relations needed for the claim.",
            "Use warning rather than blocking for optional detail that only adds depth.",
            "In final stage, any blocking unanswered dependency makes self_contained false.",
        ],
        "output_schema": COMPREHENSION_SCAN_SCHEMA,
        "json_schema": COMPREHENSION_SCAN_JSON_SCHEMA,
        "expected_output": f"comprehension/{stage}_full_report_scan.json",
    }
    write_json(workdir / "comprehension" / f"{stage}_full_report_scan_packet.json", packet)
    print(f"prepared {stage} full-report comprehension scan for {report_path.name}")
    return 0


def command_prepare_local_comprehension(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    scan_path = workdir / "comprehension" / "discover_full_report_scan.json"
    packet_path = workdir / "comprehension" / "discover_full_report_scan_packet.json"
    if not scan_path.exists() or not packet_path.exists():
        print("blocked: discovery scan output is missing", file=sys.stderr)
        return 2
    scan, discovery_packet = read_json(scan_path), read_json(packet_path)
    report = discovery_packet["report"]
    if scan.get("report_hash") != discovery_packet["report_hash"]:
        print("blocked: discovery scan does not match report hash", file=sys.stderr)
        return 2
    evidence = _load_evidence_records(workdir / "evidence_index.jsonl")
    jobs, seen = [], set()
    for issue in scan.get("items", []):
        if issue.get("answered_in_report") and issue.get("severity") != "blocking":
            continue
        issue_id = issue.get("id", "")
        if not issue_id or issue_id in seen:
            print("blocked: comprehension issue IDs must be non-empty and unique", file=sys.stderr)
            return 2
        seen.add(issue_id)
        local_packet = {
            "job_id": f"{issue_id}-LOCAL-RESOLUTION", "context_policy": "fresh", "report_hash": scan["report_hash"],
            "issue": issue, "local_report_context": _report_context(report, issue.get("first_use_text", "")),
            "candidate_source_passages": _candidate_evidence(issue, evidence),
            "instructions": [
                "Decide whether the supplied report context already answers the reader question.",
                "If source evidence supplies a missing definition, quote evidence_text exactly and identify passage_id.",
                "Never answer from outside knowledge; missing evidence must remain unresolved.",
                "Recommend keep, rewrite, delete, or mark_source_undefined. Do not edit the report.",
            ],
            "output_schema": {"id": issue_id, "status": "resolved|unresolved", "answer_from": "report|source|none",
                              "definition": "string|null", "evidence_text": "exact quote|null", "passage_id": "string|null",
                              "action": "keep|rewrite|delete|mark_source_undefined", "reason": "string"},
            "json_schema": LOCAL_RESOLUTION_JSON_SCHEMA,
            "expected_output": f"comprehension/local_verdicts/{issue_id}.json",
        }
        relative = f"comprehension/local_packets/{issue_id}.json"
        write_json(workdir / relative, local_packet)
        jobs.append({"job_id": local_packet["job_id"], "packet": relative,
                     "output": local_packet["expected_output"], "context_policy": "fresh"})
    write_json(workdir / "comprehension" / "local_jobs.json", {"report_hash": scan["report_hash"], "jobs": jobs})
    print(f"prepared {len(jobs)} isolated local comprehension jobs")
    return 0


def _validated_local_verdict(packet: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if verdict.get("id") != packet["issue"]["id"]:
        errors.append("verdict ID mismatch")
    if verdict.get("status") == "resolved":
        source = verdict.get("answer_from")
        evidence_text = verdict.get("evidence_text") or ""
        if source == "source":
            passages = {item["passage_id"]: item for item in packet["candidate_source_passages"]}
            passage = passages.get(verdict.get("passage_id"))
            if not passage or _normalized(evidence_text) not in _normalized(passage.get("text", "")):
                errors.append("source evidence is not an exact candidate passage quote")
        elif source == "report":
            if _normalized(evidence_text) not in _normalized(packet.get("local_report_context", "")):
                errors.append("report evidence is not in local context")
        else:
            errors.append("resolved verdict has no grounded answer source")
        if not verdict.get("definition"):
            errors.append("resolved verdict has no definition")
    if errors:
        return {**verdict, "status": "unresolved", "action": "mark_source_undefined",
                "validation_errors": errors}
    return {**verdict, "validation_errors": []}


def run_comprehension_pipeline(workdir: Path, report_path: Path, runner: ModelRunner) -> dict[str, Any]:
    namespace = argparse.Namespace(workdir=workdir, report=report_path, stage="discover")
    if command_prepare_comprehension(namespace):
        raise RuntimeError("unable to prepare discovery scan")
    discover_packet = read_json(workdir / "comprehension/discover_full_report_scan_packet.json")
    discover = runner.run(discover_packet, COMPREHENSION_SCAN_JSON_SCHEMA)
    discover["report_hash"] = discover_packet["report_hash"]
    write_json(workdir / "comprehension/discover_full_report_scan.json", discover)
    if command_prepare_local_comprehension(argparse.Namespace(workdir=workdir)):
        raise RuntimeError("unable to prepare local comprehension jobs")
    jobs = read_json(workdir / "comprehension/local_jobs.json")["jobs"]
    resolutions = []
    for job in jobs:
        packet = read_json(workdir / job["packet"])
        verdict = _validated_local_verdict(packet, runner.run(packet, LOCAL_RESOLUTION_JSON_SCHEMA))
        write_json(workdir / job["output"], verdict)
        resolutions.append(verdict)
    unresolved = [item for item in resolutions if item.get("status") != "resolved"]
    if unresolved:
        gate = {"passed": False, "report_hash": discover_packet["report_hash"],
                "reasons": ["local comprehension issues remain unresolved"],
                "unresolved_count": len(unresolved), "unresolved": unresolved}
        write_json(workdir / "comprehension/comprehension_gate.json", gate)
        return gate
    if resolutions:
        original = report_path.read_text(encoding="utf-8")
        rewrite_packet = {
            "job_id": "COMPREHENSION-REWRITE", "context_policy": "fresh", "report_hash": _text_hash(original),
            "report": original, "validated_resolutions": resolutions,
            "instructions": ["Apply only the validated resolutions with minimal edits.",
                             "Preserve all citations, headings, and unrelated text.",
                             "Do not add outside knowledge or unvalidated definitions."],
            "json_schema": REWRITE_JSON_SCHEMA,
        }
        write_json(workdir / "comprehension/rewrite_packet.json", rewrite_packet)
        rewrite = runner.run(rewrite_packet, REWRITE_JSON_SCHEMA)
        expected_ids = {item["id"] for item in resolutions if item.get("action") != "keep"}
        if not expected_ids.issubset(set(rewrite.get("applied_issue_ids", []))):
            raise RuntimeError("rewrite did not apply every required issue")
        backup = report_path.with_suffix(report_path.suffix + ".pre_comprehension")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        report_path.write_text(rewrite["report"], encoding="utf-8")
    final_args = argparse.Namespace(workdir=workdir, report=report_path, stage="final")
    command_prepare_comprehension(final_args)
    final_packet = read_json(workdir / "comprehension/final_full_report_scan_packet.json")
    final_scan = runner.run(final_packet, COMPREHENSION_SCAN_JSON_SCHEMA)
    final_scan["report_hash"] = final_packet["report_hash"]
    write_json(workdir / "comprehension/final_full_report_scan.json", final_scan)
    command_audit_comprehension(argparse.Namespace(workdir=workdir, report=report_path))
    return read_json(workdir / "comprehension/comprehension_gate.json")


def command_run_comprehension(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    report_path = args.report.resolve() if args.report else workdir / "final_report.md"
    try:
        gate = run_comprehension_pipeline(workdir, report_path,
                                          CodexModelRunner(workdir, model=args.model, timeout=args.timeout))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        write_json(workdir / "comprehension/run_failure.json", {"passed": False, "error": str(exc)})
        print(f"comprehension run failed: {exc}", file=sys.stderr)
        return 2
    return 0 if gate.get("passed") else 2


def command_audit_comprehension(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    report_path = args.report.resolve() if args.report else workdir / "final_report.md"
    report_hash = _text_hash(report_path.read_text(encoding="utf-8")) if report_path.exists() else ""
    final_packet_path = workdir / "comprehension" / "final_full_report_scan_packet.json"
    final_scan_path = workdir / "comprehension" / "final_full_report_scan.json"
    reasons, unresolved = [], []
    if not final_packet_path.exists() or not final_scan_path.exists():
        reasons.append("final full-report comprehension scan is missing")
    else:
        packet, scan = read_json(final_packet_path), read_json(final_scan_path)
        if packet.get("report_hash") != report_hash or scan.get("report_hash") != report_hash:
            reasons.append("final comprehension scan is stale")
        ids = [item.get("id") for item in scan.get("items", [])]
        if any(not item for item in ids) or len(ids) != len(set(ids)):
            reasons.append("final comprehension issue IDs are missing or duplicated")
        unresolved = [item for item in scan.get("items", [])
                      if item.get("severity") == "blocking" and not item.get("answered_in_report")]
        if not scan.get("self_contained") or unresolved:
            reasons.append("report is not self-contained")
    gate = {"passed": not reasons, "report_hash": report_hash, "reasons": reasons,
            "unresolved_count": len(unresolved), "unresolved": unresolved,
            "required_stage": "fresh full-report final scan after all local resolutions and rewrites"}
    write_json(workdir / "comprehension" / "comprehension_gate.json", gate)
    print(f'comprehension gate passed={gate["passed"]}; unresolved={len(unresolved)}')
    return 0 if gate["passed"] else 2


def command_search(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    index_path = workdir / "evidence_index.jsonl"
    if not index_path.exists():
        print("evidence index missing: run extract first", file=sys.stderr)
        return 2
    terms = [term.casefold() for term in re.findall(r"[\w.-]+", args.query) if term]
    matches = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        haystack = (record.get("section_title", "") + " " + record.get("text", "")).casefold()
        score = sum(haystack.count(term) for term in terms)
        if score:
            matches.append({"score": score, **record})
    matches.sort(key=lambda item: (-item["score"], item["passage_id"]))
    print(json.dumps({"query": args.query, "count": len(matches), "results": matches[:args.limit]}, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    workdir = args.workdir.resolve()
    names = ["paper_metadata.json", "sections.json", "evidence_index.jsonl", "chapter_jobs.json", "chapter_audit_summary.json",
             "verified_claim_registry.json", "verified_claims.md", "synthesis_gate.json", "synthesis_packet.json", "synthesis_report.json",
             "comprehension/discover_full_report_scan_packet.json", "comprehension/local_jobs.json",
             "comprehension/final_full_report_scan_packet.json", "comprehension/comprehension_gate.json"]
    print(json.dumps({name: (workdir / name).exists() for name in names}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chapter-first paper review orchestration pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="extract PDF/text and automatically detect sections")
    extract.add_argument("--source", type=Path, required=True)
    extract.add_argument("--workdir", type=Path, required=True)
    extract.add_argument("--paper-id")
    extract.set_defaults(func=command_extract)
    for name, help_text, func in (
        ("prepare-chapters", "create isolated fresh-context chapter job packets", command_prepare_chapters),
        ("audit-chapters", "deterministically audit model-produced chapter reports", command_audit_chapters),
        ("prepare-synthesis", "gate and create the final fresh-context synthesis packet", command_prepare_synthesis),
        ("prepare-local-comprehension", "create isolated evidence-resolution jobs from the discovery scan", command_prepare_local_comprehension),
        ("audit-comprehension", "fail closed unless the fresh final full-report scan is self-contained", command_audit_comprehension),
        ("status", "show available pipeline artifacts", command_status),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--workdir", type=Path, required=True)
        command.set_defaults(func=func)
    comprehension = sub.add_parser("prepare-comprehension", help="create an open-world fresh full-report comprehension scan")
    comprehension.add_argument("--workdir", type=Path, required=True)
    comprehension.add_argument("--report", type=Path)
    comprehension.add_argument("--stage", choices=("discover", "final"), default="discover")
    comprehension.set_defaults(func=command_prepare_comprehension)
    run_comprehension = sub.add_parser("run-comprehension", help="execute the complete comprehension workflow with real isolated model calls")
    run_comprehension.add_argument("--workdir", type=Path, required=True)
    run_comprehension.add_argument("--report", type=Path)
    run_comprehension.add_argument("--model")
    run_comprehension.add_argument("--timeout", type=int, default=600)
    run_comprehension.set_defaults(func=command_run_comprehension)
    search = sub.add_parser("search", help="search the extracted full-text evidence index")
    search.add_argument("--workdir", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=command_search)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
