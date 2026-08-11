"""
Report Auditor
==============

Final-pass audit tool for Chinese paper reports.

It scores evidence fidelity, hallucination risk, inference risk, citation usage,
numeric faithfulness signals, required-section coverage, and mojibake.
All audit metrics are calculated deterministically in this tool; the model should
not manually calculate pass/fail scores or weighted audit numbers.
The deterministic layer checks mojibake, Query Matrix leakage, section coverage,
citations, URL/DOI/arXiv format, optional URL reachability, source-number
matching, Markdown tables, duplicate references, report length, Chinese/English
ratio, vague strong words, and undefined acronyms.

Typical usage:
  $env:PYTHONUTF8="1"; python tools/report_auditor.py report.md --json-out audit.json
  python tools/report_auditor.py report.md --source paper.txt --write
  python tools/report_auditor.py report.md --json-out audit.json
  python tools/report_auditor.py report.md --semantic-packet
  python tools/report_auditor.py report.md --no-verify-citation-metadata --json-out audit.json
  python tools/report_auditor.py report.md --check-links --json-out audit.json

Exit code:
  0 = audit completed and returned a full result, even when the report fails.
  2 = report failed only when --fail-on-audit-fail is explicitly provided.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_THRESHOLD = 80.0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REQUIRED_SECTIONS = [
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

MOJIBAKE_MARKERS = ["�", "嚗", "蝣", "銝", "撠", "?", "?"]

CITATION_PATTERNS = [
    r"\[[^\]]+(?:et al\.|arXiv|DOI|Section|Table|Figure|S2ID|頁|章|節)[^\]]*\]",
    r"\(.*?(?:19|20)\d{2}.*?\)",
    r"arXiv:\d{4}\.\d{4,5}",
    r"DOI:\s*10\.\S+",
]

CLAIM_SPLIT_RE = re.compile(r"(?<=[。！？；;.!?])\s+|\n+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
NUMBER_RE = re.compile(r"(?<![\w.])(?:\d+(?:\.\d+)?%?|\d+x|[1-9]\d{3})(?![\w.])", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s\])>\"']+")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
ARXIV_RE = re.compile(r"\b(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
REFERENCE_HEADING_RE = re.compile(r"^#{1,6}\s+.*(?:參考|References|Bibliography)", re.IGNORECASE | re.MULTILINE)
ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,}\b")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
META_RE = re.compile(
    r"<meta\s+(?:name|property)=[\"']([^\"']+)[\"']\s+content=[\"']([^\"']*)[\"'][^>]*>",
    re.IGNORECASE,
)
META_TAG_RE = re.compile(r"<meta\s+[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"([a-zA-Z_:.-]+)=[\"']([^\"']*)[\"']")
TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
CHINESE_TITLE_RE = re.compile(r"《([^》]{4,180})》")
QUOTED_TITLE_RE = re.compile(r"[\"“][*_]?([^\"”]{8,220})[*_]?[\"”]")
BRACKET_CITATION_RE = re.compile(r"\[([^\]]{3,160})\]")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

HIGH_RISK_WORDS = [
    "證明",
    "必然",
    "一定",
    "完全",
    "最優",
    "顯著提升",
    "導致",
    "造成",
    "使得",
    "因此",
    "所以",
    "代表",
    "可泛化",
    "適用於所有",
]

VAGUE_STRONG_WORDS = [
    "100%",
    "精準",
    "權威",
    "最具代表性",
    "最新",
    "頂尖",
    "突破",
    "顯著",
    "大幅",
    "完全",
    "必須",
    "證明",
    "無法",
]

KNOWN_ACRONYMS = {
    "ACL",
    "AI",
    "API",
    "ASCII",
    "CNN",
    "DOI",
    "EMNLP",
    "F1",
    "GNN",
    "GPU",
    "HTML",
    "IEEE",
    "JSON",
    "KG",
    "KGC",
    "LLM",
    "NLP",
    "PDF",
    "QA",
    "RAG",
    "URL",
}

URL_DOMAIN_LABELS = {
    "aclanthology.org": {"ACL", "EMNLP", "NAACL", "COLING"},
    "arxiv.org": {"arXiv"},
    "doi.org": {"DOI"},
    "github.com": {"GitHub"},
    "ieeexplore.ieee.org": {"IEEE"},
    "nature.com": {"Nature", "Scientific Data"},
}

SOURCE_LABELS = {"ACL", "EMNLP", "NAACL", "COLING", "arXiv", "DOI", "GitHub", "IEEE", "Nature", "Scientific Data"}

INFERENCE_WORDS = [
    "可能",
    "推測",
    "可視為",
    "可理解為",
    "意味著",
    "因此",
    "所以",
    "代表",
    "適合",
    "建議",
    "可用於",
    "論文未明說",
    "實作時可",
]

DIRECT_EVIDENCE_WORDS = [
    "論文指出",
    "作者指出",
    "原文指出",
    "實驗顯示",
    "Table",
    "Figure",
    "Section",
    "表",
    "圖",
    "第",
]


@dataclass
class Finding:
    severity: str
    category: str
    score_impact: float
    message: str
    context: str
    rewrite_hint: str


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp950"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def has_citation(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in CITATION_PATTERNS)


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def source_contains_number(source: str, number: str) -> bool:
    if not source:
        return True
    normalized_source = normalize_for_match(source)
    candidates = {number}
    if number.endswith("%"):
        candidates.add(number[:-1])
    if number.lower().endswith("x"):
        candidates.add(number[:-1])
    return any(candidate.lower() in normalized_source for candidate in candidates if candidate)


def split_claims(report: str) -> list[str]:
    claims = []
    for part in CLAIM_SPLIT_RE.split(report):
        line = part.strip()
        if not line:
            continue
        if line.startswith("|") or line.startswith("```"):
            continue
        if re.match(r"^#{1,6}\s+", line):
            continue
        if len(line) < 12:
            continue
        claims.append(line)
    return claims


def get_sections(report: str) -> list[str]:
    return [match.group(2).strip() for match in HEADING_RE.finditer(report)]


def context_window(report: str, needle: str, width: int = 220) -> str:
    index = report.find(needle)
    if index < 0:
        return needle[: width * 2]
    start = max(0, index - width)
    end = min(len(report), index + len(needle) + width)
    return report[start:end].replace("\n", " ").strip()


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 100.0
    return 100.0 * numerator / denominator


def extract_urls(report: str) -> list[str]:
    return sorted(set(URL_RE.findall(report)))


def extract_dois(report: str) -> list[str]:
    return sorted(set(match.group(0).rstrip(".,;") for match in DOI_RE.finditer(report)))


def extract_arxiv_ids(report: str) -> list[str]:
    return sorted(set(match.group(1) for match in ARXIV_RE.finditer(report)))


def check_url_status(url: str, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "report-auditor/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"url": url, "ok": 200 <= response.status < 400, "status": response.status, "error": ""}
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            try:
                request = urllib.request.Request(url, method="GET", headers={"User-Agent": "report-auditor/1.0"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return {"url": url, "ok": 200 <= response.status < 400, "status": response.status, "error": ""}
            except Exception as get_exc:
                return {"url": url, "ok": False, "status": 0, "error": str(get_exc)}
        return {"url": url, "ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"url": url, "ok": False, "status": 0, "error": str(exc)}


def fetch_url_text(url: str, timeout: float = 10.0, max_bytes: int = 400_000) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "report-auditor/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1)
        raw = response.read(max_bytes)
    return raw.decode(charset, errors="replace"), content_type


def parse_page_metadata(page: str) -> dict:
    meta: dict[str, list[str]] = {}
    for name, value in META_RE.findall(page):
        meta.setdefault(name.lower(), []).append(html.unescape(value.strip()))
    for tag in META_TAG_RE.findall(page):
        attrs = {name.lower(): value for name, value in ATTR_RE.findall(tag)}
        name = attrs.get("name") or attrs.get("property")
        value = attrs.get("content")
        if name and value:
            meta.setdefault(name.lower(), []).append(html.unescape(value.strip()))

    title = ""
    for key in ("citation_title", "dc.title", "og:title", "twitter:title"):
        if meta.get(key):
            title = meta[key][0]
            break
    if not title:
        match = TITLE_TAG_RE.search(page)
        if match:
            title = html.unescape(re.sub(r"\s+", " ", match.group(1)).strip())

    authors = []
    for key in ("citation_author", "dc.creator", "author"):
        authors.extend(meta.get(key, []))

    year = ""
    for key in ("citation_publication_date", "citation_online_date", "dc.date", "article:published_time"):
        if meta.get(key):
            year_match = YEAR_RE.search(meta[key][0])
            if year_match:
                year = year_match.group(0)
                break

    doi = ""
    for key in ("citation_doi", "dc.identifier"):
        for value in meta.get(key, []):
            doi_match = DOI_RE.search(value)
            if doi_match:
                doi = doi_match.group(0)
                break
        if doi:
            break

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
    }


def normalize_title(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def token_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize_title(left).split())
    right_tokens = set(normalize_title(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def title_matches_claim(claimed_title: str, actual_title: str) -> bool:
    normalized_claim = normalize_title(claimed_title)
    normalized_actual = normalize_title(actual_title)
    if not normalized_claim or not normalized_actual:
        return True
    if normalized_claim in normalized_actual:
        return True
    return token_similarity(claimed_title, actual_title) >= 0.55


def url_domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")


def expected_labels_for_url(url: str) -> set[str]:
    domain = url_domain(url)
    labels: set[str] = set()
    for known_domain, domain_labels in URL_DOMAIN_LABELS.items():
        if domain.endswith(known_domain):
            labels |= domain_labels
    return labels


def metadata_context_for_url(report: str, url: str) -> str:
    lines = report.splitlines()
    for index, line in enumerate(lines):
        if url not in line:
            continue
        stripped = line.strip()
        if stripped.startswith("|"):
            return stripped
        start = index
        while start > 0 and lines[start - 1].strip():
            start -= 1
        end = index + 1
        while end < len(lines) and lines[end].strip():
            end += 1
        paragraph = " ".join(part.strip() for part in lines[start:end] if part.strip())
        if len(paragraph) <= 900:
            return paragraph
        return context_window(report, url, width=260)
    return context_window(report, url, width=260)


def claimed_metadata_from_context(context: str) -> dict:
    titles = [title.strip("*_ ") for title in CHINESE_TITLE_RE.findall(context) + QUOTED_TITLE_RE.findall(context)]
    bracket_texts = BRACKET_CITATION_RE.findall(context)
    labels = sorted(label for label in SOURCE_LABELS if re.search(rf"\b{re.escape(label)}\b", context, re.IGNORECASE))
    years = sorted(set(match.group(0) for match in YEAR_RE.finditer(context)))
    first_author = ""
    for item in bracket_texts:
        match = re.search(r"([A-Z][A-Za-z-]+)\s+et\s+al\.", item)
        if match:
            first_author = match.group(1)
            break
    return {
        "titles": titles,
        "bracket_citations": bracket_texts,
        "labels": labels,
        "years": years,
        "first_author": first_author,
    }


def author_surname(author: str) -> str:
    author = re.sub(r"\s+", " ", author).strip()
    if "," in author:
        return author.split(",", 1)[0].strip().lower()
    return author.split()[-1].lower() if author else ""


def title_candidates_from_brackets(bracket_texts: list[str]) -> list[str]:
    candidates = []
    skip_words = {
        "ACL",
        "Anthology",
        "Ant",
        "DOI",
        "EMNLP",
        "GitHub",
        "IEEE",
        "Nature",
        "Scientific",
        "Data",
        "arXiv",
    }
    for item in bracket_texts:
        if "http" in item or "et al" in item:
            continue
        candidate = re.sub(r"\b(?:19|20)\d{2}\b", " ", item)
        candidate = re.sub(r"\b\d{4}\.\d{4,5}\b", " ", candidate)
        for label in SOURCE_LABELS:
            candidate = re.sub(rf"\b{re.escape(label)}\b", " ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"[:,/|()\[\]]+", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip(" -*_")
        tokens = [token for token in candidate.split() if token not in skip_words]
        if len(" ".join(tokens)) >= 8:
            candidates.append(" ".join(tokens))
    return candidates


def verify_citation_metadata(report: str, urls: list[str], timeout: float = 10.0) -> list[dict]:
    results = []
    for url in urls:
        context = metadata_context_for_url(report, url)
        claimed = claimed_metadata_from_context(context)
        expected_labels = expected_labels_for_url(url)

        result = {
            "url": url,
            "domain": url_domain(url),
            "context": context,
            "claimed": claimed,
            "expected_labels": sorted(expected_labels),
            "metadata": {},
            "ok": True,
            "issues": [],
            "error": "",
        }

        try:
            page, content_type = fetch_url_text(url, timeout=timeout)
            metadata = parse_page_metadata(page)
            metadata["content_type"] = content_type
            result["metadata"] = metadata
        except Exception as exc:
            result["ok"] = False
            result["error"] = str(exc)
            result["issues"].append("無法抓取頁面 metadata")
            results.append(result)
            continue

        actual_title = result["metadata"].get("title", "")
        for claimed_title in claimed["titles"] + title_candidates_from_brackets(claimed["bracket_citations"]):
            if actual_title and not title_matches_claim(claimed_title, actual_title):
                result["issues"].append(
                    f"宣稱標題 `{claimed_title}` 與頁面標題 `{actual_title}` 相似度過低"
                )

        actual_authors = result["metadata"].get("authors", [])
        if claimed["first_author"] and actual_authors:
            actual_first = author_surname(actual_authors[0])
            if claimed["first_author"].lower() != actual_first:
                result["issues"].append(
                    f"宣稱第一作者 `{claimed['first_author']}` 與頁面第一作者 `{actual_authors[0]}` 不一致"
                )

        actual_year = result["metadata"].get("year", "")
        if actual_year and claimed["years"] and actual_year not in claimed["years"]:
            result["issues"].append(f"宣稱年份 {claimed['years']} 與頁面年份 `{actual_year}` 不一致")

        result["ok"] = not result["issues"]
        results.append(result)
    return results


def citation_metadata_mismatches(citation_metadata: list[dict]) -> list[dict]:
    return [item for item in citation_metadata if item.get("issues") and not item.get("error")]


def citation_metadata_fetch_errors(citation_metadata: list[dict]) -> list[dict]:
    return [item for item in citation_metadata if item.get("error")]


def parse_markdown_tables(report: str) -> list[dict]:
    tables = []
    lines = report.splitlines()
    index = 0
    while index < len(lines) - 1:
        header = lines[index].strip()
        separator = lines[index + 1].strip()
        if header.startswith("|") and separator.startswith("|") and re.search(r"\|?\s*:?-{3,}:?\s*\|", separator):
            rows = [header, separator]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(lines[index].strip())
                index += 1
            header_cols = [col.strip() for col in header.strip("|").split("|")]
            row_col_counts = [len(row.strip("|").split("|")) for row in rows[2:]]
            tables.append(
                {
                    "header": header,
                    "columns": header_cols,
                    "column_count": len(header_cols),
                    "row_count": len(rows) - 2,
                    "bad_row_count": sum(1 for count in row_col_counts if count != len(header_cols)),
                    "empty_header_cells": sum(1 for col in header_cols if not col),
                }
            )
            continue
        index += 1
    return tables


def extract_reference_lines(report: str) -> list[str]:
    match = REFERENCE_HEADING_RE.search(report)
    if not match:
        return []
    tail = report[match.end() :]
    next_heading = HEADING_RE.search(tail)
    if next_heading:
        tail = tail[: next_heading.start()]
    refs = []
    for line in tail.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:[-*]|\d+[.)])\s+", stripped):
            refs.append(stripped)
    return refs


def normalize_reference(ref: str) -> str:
    ref = re.sub(r"https?://\S+", "", ref)
    ref = re.sub(r"[\W_]+", " ", ref.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", ref).strip()


def find_duplicate_references(reference_lines: list[str]) -> list[dict]:
    seen: dict[str, str] = {}
    duplicates = []
    for ref in reference_lines:
        normalized = normalize_reference(ref)
        if not normalized:
            continue
        key = normalized[:120]
        if key in seen:
            duplicates.append({"first": seen[key], "duplicate": ref})
        else:
            seen[key] = ref
    return duplicates


def report_length_stats(report: str, sections: list[str]) -> dict:
    chars = len(report)
    words = len(re.findall(r"\S+", report))
    heading_matches = list(HEADING_RE.finditer(report))
    section_lengths = []
    for idx, match in enumerate(heading_matches):
        start = match.end()
        end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(report)
        section_lengths.append({"section": match.group(2).strip(), "chars": end - start})
    short_sections = [item for item in section_lengths if item["chars"] < 120]
    return {
        "chars": chars,
        "words": words,
        "section_count": len(sections),
        "short_sections": short_sections[:20],
    }


def language_ratio(report: str) -> dict:
    cjk_count = len(CJK_RE.findall(report))
    latin_count = len(LATIN_RE.findall(report))
    total = cjk_count + latin_count
    return {
        "cjk_chars": cjk_count,
        "latin_chars": latin_count,
        "chinese_ratio": round(percent(cjk_count, total), 2) if total else 0.0,
        "english_ratio": round(percent(latin_count, total), 2) if total else 0.0,
    }


def find_vague_strong_words(report: str) -> list[dict]:
    hits = []
    for word in VAGUE_STRONG_WORDS:
        for match in re.finditer(re.escape(word), report):
            hits.append({"word": word, "context": context_window(report, word, width=120)})
            break
    return hits


def find_undefined_acronyms(report: str) -> list[str]:
    acronyms = sorted(set(ACRONYM_RE.findall(report)))
    undefined = []
    for acronym in acronyms:
        if acronym in KNOWN_ACRONYMS or len(acronym) < 2 or acronym.isdigit():
            continue
        define_before = re.search(rf"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z\s-]{{2,80}}\({re.escape(acronym)}\)", report)
        define_after = re.search(rf"{re.escape(acronym)}\s*[（(][^\n）)]{{2,80}}[）)]", report)
        if not define_before and not define_after:
            undefined.append(acronym)
    return undefined[:50]


def deterministic_audit(
    report: str,
    source: str,
    sections: list[str],
    check_links: bool,
    verify_citation_metadata_enabled: bool,
) -> dict:
    urls = extract_urls(report)
    dois = extract_dois(report)
    arxiv_ids = extract_arxiv_ids(report)
    tables = parse_markdown_tables(report)
    reference_lines = extract_reference_lines(report)
    duplicate_refs = find_duplicate_references(reference_lines)
    length = report_length_stats(report, sections)
    lang = language_ratio(report)
    vague_words = find_vague_strong_words(report)
    undefined_acronyms = find_undefined_acronyms(report)
    url_status = [check_url_status(url) for url in urls] if check_links else []
    citation_metadata = verify_citation_metadata(report, urls) if verify_citation_metadata_enabled else []

    doi_urls_missing = [doi for doi in dois if f"https://doi.org/{doi}".lower() not in report.lower()]
    arxiv_urls_missing = [
        arxiv_id
        for arxiv_id in arxiv_ids
        if f"arxiv.org/abs/{arxiv_id}".lower() not in report.lower()
        and f"arxiv.org/pdf/{arxiv_id}".lower() not in report.lower()
    ]
    bad_tables = [table for table in tables if table["bad_row_count"] or table["empty_header_cells"]]

    return {
        "mojibake_markers": [marker for marker in MOJIBAKE_MARKERS if marker in report],
        "query_matrix_present": "Query Matrix" in report or "query matrix" in report.lower(),
        "section_count": len(sections),
        "url_count": len(urls),
        "urls": urls,
        "url_status": url_status,
        "citation_metadata_verification_enabled": verify_citation_metadata_enabled,
        "citation_metadata": citation_metadata,
        "citation_metadata_mismatches": citation_metadata_mismatches(citation_metadata),
        "citation_metadata_fetch_errors": citation_metadata_fetch_errors(citation_metadata),
        "doi_count": len(dois),
        "dois": dois,
        "doi_urls_missing": doi_urls_missing,
        "arxiv_id_count": len(arxiv_ids),
        "arxiv_ids": arxiv_ids,
        "arxiv_urls_missing": arxiv_urls_missing,
        "table_count": len(tables),
        "bad_tables": bad_tables,
        "reference_count": len(reference_lines),
        "duplicate_references": duplicate_refs,
        "length": length,
        "language_ratio": lang,
        "vague_strong_words": vague_words,
        "undefined_acronyms": undefined_acronyms,
        "source_number_check_enabled": bool(source),
        "link_check_enabled": check_links,
    }


def audit_report(
    report: str,
    source: str = "",
    threshold: float = DEFAULT_THRESHOLD,
    check_links: bool = False,
    verify_citation_metadata_enabled: bool = True,
) -> dict:
    claims = split_claims(report)
    sections = get_sections(report)
    findings: list[Finding] = []
    deterministic = deterministic_audit(
        report,
        source,
        sections,
        check_links,
        verify_citation_metadata_enabled,
    )

    mojibake_hits = deterministic["mojibake_markers"]
    if mojibake_hits:
        findings.append(
            Finding(
                severity="critical",
                category="mojibake",
                score_impact=20,
                message=f"報告含有疑似亂碼標記：{', '.join(mojibake_hits)}",
                context="; ".join(mojibake_hits),
                rewrite_hint="重新以 UTF-8 讀寫報告，並修復出現亂碼的段落。",
            )
        )

    if deterministic["query_matrix_present"]:
        findings.append(
            Finding(
                severity="major",
                category="format",
                score_impact=12,
                message="最終報告不應包含 Query Matrix。",
                context=context_window(report, "Query Matrix"),
                rewrite_hint="刪除 Query Matrix；只保留搜尋方法或資料來源摘要。",
            )
        )

    missing_sections = [
        required
        for required in REQUIRED_SECTIONS
        if not any(required in section for section in sections)
    ]
    for section in missing_sections:
        findings.append(
            Finding(
                severity="major",
                category="coverage",
                score_impact=4,
                message=f"缺少必要章節：{section}",
                context="; ".join(sections[:12]),
                rewrite_hint=f"補上 `{section}` 章節，並加入證據或明確標記推論。",
            )
        )

    for doi in deterministic["doi_urls_missing"][:10]:
        findings.append(
            Finding(
                severity="minor",
                category="doi_format",
                score_impact=1,
                message=f"DOI 有出現但缺少 doi.org 標準連結：{doi}",
                context=context_window(report, doi),
                rewrite_hint=f"補上 `https://doi.org/{doi}`，方便機器驗證與讀者追溯。",
            )
        )

    for arxiv_id in deterministic["arxiv_urls_missing"][:10]:
        findings.append(
            Finding(
                severity="minor",
                category="arxiv_format",
                score_impact=1,
                message=f"arXiv ID 有出現但缺少 arxiv.org 標準連結：{arxiv_id}",
                context=context_window(report, arxiv_id),
                rewrite_hint=f"補上 `https://arxiv.org/abs/{arxiv_id}`。",
            )
        )

    for status in deterministic["url_status"]:
        if not status["ok"]:
            findings.append(
                Finding(
                    severity="major",
                    category="url_reachability",
                    score_impact=4,
                    message=f"URL 無法正常連線：{status['url']}",
                    context=status["error"] or f"HTTP status {status['status']}",
                    rewrite_hint="更換為官方可連結頁面，或移除此 URL。",
                )
            )

    for item in deterministic["citation_metadata_mismatches"][:12]:
        findings.append(
            Finding(
                severity="major",
                category="citation_url_mismatch",
                score_impact=5,
                message=f"引用內容與 URL metadata 疑似不一致：{item['url']}",
                context=" | ".join(item["issues"]) + " | " + item["context"],
                rewrite_hint="用抓到的頁面 metadata 校正 title、作者、年份或來源標籤；若 URL 錯誤，改成正確官方頁面。",
            )
        )

    for item in deterministic["citation_metadata_fetch_errors"][:8]:
        findings.append(
            Finding(
                severity="minor",
                category="citation_metadata_unavailable",
                score_impact=1,
                message=f"無法抓取引用頁面 metadata：{item['url']}",
                context=f"{item['error']} | {item['context']}",
                rewrite_hint="若環境可連網請重跑審核；若頁面封鎖 metadata，手動確認並補上官方 DOI/arXiv/出版頁。",
            )
        )

    for table in deterministic["bad_tables"][:8]:
        findings.append(
            Finding(
                severity="major",
                category="table_integrity",
                score_impact=3,
                message="Markdown 表格欄位數不一致或表頭有空欄。",
                context=table["header"],
                rewrite_hint="修正表格列的欄位數，確保每列與表頭欄位一致。",
            )
        )

    for duplicate in deterministic["duplicate_references"][:8]:
        findings.append(
            Finding(
                severity="minor",
                category="duplicate_reference",
                score_impact=1,
                message="參考文獻疑似重複。",
                context=f"first={duplicate['first']} | duplicate={duplicate['duplicate']}",
                rewrite_hint="合併重複 reference，保留一條完整且可驗證的引用。",
            )
        )

    for item in deterministic["length"]["short_sections"][:8]:
        findings.append(
            Finding(
                severity="minor",
                category="section_length",
                score_impact=1,
                message=f"章節內容過短：{item['section']}",
                context=f"{item['chars']} chars",
                rewrite_hint="補充該章節的證據、解釋、限制或實作細節。",
            )
        )

    if deterministic["language_ratio"]["chinese_ratio"] < 55 and deterministic["length"]["chars"] > 1000:
        findings.append(
            Finding(
                severity="minor",
                category="language_ratio",
                score_impact=2,
                message="中文比例偏低，可能不符合中文報告要求。",
                context=json.dumps(deterministic["language_ratio"], ensure_ascii=False),
                rewrite_hint="將摘要、分析、表格說明與結論改為中文；英文保留在術語或 citation。",
            )
        )

    for hit in deterministic["vague_strong_words"][:10]:
        findings.append(
            Finding(
                severity="minor",
                category="vague_strong_word",
                score_impact=1,
                message=f"出現空泛或過強措辭：{hit['word']}",
                context=hit["context"],
                rewrite_hint="若有直接證據，補 citation；否則改成更保守、可驗證的措辭。",
            )
        )

    if deterministic["undefined_acronyms"]:
        findings.append(
            Finding(
                severity="minor",
                category="undefined_acronym",
                score_impact=2,
                message="偵測到可能未定義縮寫。",
                context=", ".join(deterministic["undefined_acronyms"][:20]),
                rewrite_hint="第一次出現縮寫時補上中文/英文全名，例如 `檢索增強生成（RAG）`。",
            )
        )

    cited_claims = [claim for claim in claims if has_citation(claim)]
    unsupported_claims = []
    high_risk_claims = []
    inference_claims = []
    direct_claims_without_citation = []

    for claim in claims:
        claim_has_citation = has_citation(claim)
        has_high_risk = any(word in claim for word in HIGH_RISK_WORDS)
        has_inference = any(word in claim for word in INFERENCE_WORDS)
        claims_direct_evidence = any(word in claim for word in DIRECT_EVIDENCE_WORDS)

        if not claim_has_citation and (has_high_risk or claims_direct_evidence or len(claim) > 80):
            unsupported_claims.append(claim)
        if has_high_risk and not claim_has_citation:
            high_risk_claims.append(claim)
        if has_inference:
            inference_claims.append(claim)
        if claims_direct_evidence and not claim_has_citation:
            direct_claims_without_citation.append(claim)

    for claim in unsupported_claims[:10]:
        findings.append(
            Finding(
                severity="major",
                category="unsupported_claim",
                score_impact=3,
                message="重要主張缺少 citation。",
                context=context_window(report, claim),
                rewrite_hint="補上原論文章節/表格/URL citation；若找不到證據，改寫為推論或刪除。",
            )
        )

    for claim in high_risk_claims[:8]:
        findings.append(
            Finding(
                severity="critical",
                category="high_risk_inference",
                score_impact=5,
                message="高風險推論或因果語氣缺少證據。",
                context=context_window(report, claim),
                rewrite_hint="避免使用『證明、必然、導致、最優』等強語氣；補 ablation/實驗 citation，或改成保守措辭。",
            )
        )

    for claim in direct_claims_without_citation[:8]:
        findings.append(
            Finding(
                severity="major",
                category="citation_precision",
                score_impact=4,
                message="宣稱『論文指出/實驗顯示』但沒有 citation。",
                context=context_window(report, claim),
                rewrite_hint="在句尾加入具體來源，例如 `[Author et al., Year, Section x.x]`。",
            )
        )

    number_claims = []
    number_errors = []
    for claim in claims:
        numbers = NUMBER_RE.findall(claim)
        if not numbers:
            continue
        number_claims.append(claim)
        missing = [number for number in numbers if not source_contains_number(source, number)]
        if source and missing:
            number_errors.append((claim, missing))

    for claim, missing in number_errors[:8]:
        findings.append(
            Finding(
                severity="major",
                category="numeric_faithfulness",
                score_impact=4,
                message=f"報告中的數字未在來源文字中找到：{', '.join(missing)}",
                context=context_window(report, claim),
                rewrite_hint="核對原論文表格/實驗設定；若是推算數字，明確標註計算方式。",
            )
        )

    total_claims = max(len(claims), 1)
    citation_support_rate = percent(len(cited_claims), total_claims)
    unsupported_claim_rate = percent(len(unsupported_claims), total_claims)
    high_risk_inference_rate = percent(len(high_risk_claims), total_claims)
    inference_rate = percent(len(inference_claims), total_claims)
    numeric_error_rate = percent(len(number_errors), max(len(number_claims), 1)) if source else 0.0
    required_section_rate = percent(len(REQUIRED_SECTIONS) - len(missing_sections), len(REQUIRED_SECTIONS))
    link_error_rate = percent(
        sum(1 for status in deterministic["url_status"] if not status["ok"]),
        max(len(deterministic["url_status"]), 1),
    ) if check_links else 0.0
    citation_metadata_mismatch_rate = percent(
        len(deterministic["citation_metadata_mismatches"]),
        max(len(deterministic["citation_metadata"]), 1),
    ) if verify_citation_metadata_enabled else 0.0
    citation_metadata_fetch_error_rate = percent(
        len(deterministic["citation_metadata_fetch_errors"]),
        max(len(deterministic["citation_metadata"]), 1),
    ) if verify_citation_metadata_enabled else 0.0
    table_error_rate = percent(len(deterministic["bad_tables"]), max(deterministic["table_count"], 1))
    reference_duplicate_rate = percent(
        len(deterministic["duplicate_references"]),
        max(deterministic["reference_count"], 1),
    )

    evidence_score = clamp(
        100
        - unsupported_claim_rate * 1.15
        - high_risk_inference_rate * 1.6
        - numeric_error_rate * 0.8
        - link_error_rate * 0.3
        - citation_metadata_mismatch_rate * 0.5
        - citation_metadata_fetch_error_rate * 0.05
        - reference_duplicate_rate * 0.2
        - len(mojibake_hits) * 15
    )
    inference_score = clamp(100 - high_risk_inference_rate * 2.0 - max(0, inference_rate - 45) * 0.4)
    structure_score = clamp(
        required_section_rate
        - (12 if deterministic["query_matrix_present"] else 0)
        - table_error_rate * 0.3
        - min(len(deterministic["undefined_acronyms"]), 10) * 0.5
    )

    hallucination_score = clamp(
        0.50 * unsupported_claim_rate
        + 1.00 * high_risk_inference_rate
        + 0.80 * numeric_error_rate
        + 0.30 * citation_metadata_mismatch_rate
        + (20 if mojibake_hits else 0),
        0,
        100,
    )

    final_score = clamp(
        0.45 * evidence_score
        + 0.25 * inference_score
        + 0.20 * structure_score
        + 0.10 * (100 - hallucination_score)
    )

    passed = final_score >= threshold and not any(f.severity == "critical" for f in findings)

    result = {
        "passed": passed,
        "threshold": threshold,
        "final_score": round(final_score, 2),
        "scores": {
            "evidence_score": round(evidence_score, 2),
            "inference_score": round(inference_score, 2),
            "structure_score": round(structure_score, 2),
            "hallucination_score": round(hallucination_score, 2),
            "citation_support_rate": round(citation_support_rate, 2),
            "unsupported_claim_rate": round(unsupported_claim_rate, 2),
            "high_risk_inference_rate": round(high_risk_inference_rate, 2),
            "numeric_error_rate": round(numeric_error_rate, 2),
            "required_section_rate": round(required_section_rate, 2),
            "link_error_rate": round(link_error_rate, 2),
            "citation_metadata_mismatch_rate": round(citation_metadata_mismatch_rate, 2),
            "citation_metadata_fetch_error_rate": round(citation_metadata_fetch_error_rate, 2),
            "table_error_rate": round(table_error_rate, 2),
            "reference_duplicate_rate": round(reference_duplicate_rate, 2),
        },
        "counts": {
            "claims": len(claims),
            "cited_claims": len(cited_claims),
            "unsupported_claims": len(unsupported_claims),
            "high_risk_inference_claims": len(high_risk_claims),
            "number_claims": len(number_claims),
            "numeric_mismatches": len(number_errors),
            "missing_sections": len(missing_sections),
            "urls": deterministic["url_count"],
            "citation_metadata_checked": len(deterministic["citation_metadata"]),
            "citation_metadata_mismatches": len(deterministic["citation_metadata_mismatches"]),
            "citation_metadata_fetch_errors": len(deterministic["citation_metadata_fetch_errors"]),
            "dois": deterministic["doi_count"],
            "arxiv_ids": deterministic["arxiv_id_count"],
            "tables": deterministic["table_count"],
            "bad_tables": len(deterministic["bad_tables"]),
            "references": deterministic["reference_count"],
            "duplicate_references": len(deterministic["duplicate_references"]),
            "undefined_acronyms": len(deterministic["undefined_acronyms"]),
            "vague_strong_words": len(deterministic["vague_strong_words"]),
            "findings": len(findings),
        },
        "missing_sections": missing_sections,
        "deterministic_checks": deterministic,
        "rewrite_contexts": [asdict(f) for f in findings],
    }
    return result


def audit_markdown(result: dict) -> str:
    status = "通過" if result["passed"] else "未通過"
    scores = result["scores"]
    lines = [
        "",
        "## Evidence Fidelity Audit",
        "",
        f"- 審核結果：{status}",
        f"- 最終分數：{result['final_score']:.2f} / 100",
        f"- 門檻：{result['threshold']:.2f}",
        f"- 證據分數：{scores['evidence_score']:.2f}",
        f"- 推論分數：{scores['inference_score']:.2f}",
        f"- 結構分數：{scores['structure_score']:.2f}",
        f"- 幻覺風險分數：{scores['hallucination_score']:.2f}（越低越好）",
        f"- Citation 支撐率：{scores['citation_support_rate']:.2f}%",
        f"- 無支撐主張率：{scores['unsupported_claim_rate']:.2f}%",
        f"- 高風險推論率：{scores['high_risk_inference_rate']:.2f}%",
        f"- 數字錯誤率：{scores['numeric_error_rate']:.2f}%",
    ]
    return "\n".join(lines) + "\n"


def build_semantic_packet(result: dict, max_items: int = 40) -> dict:
    deterministic = result.get("deterministic_checks", {})
    semantic_categories = {
        "citation_url_mismatch",
        "citation_metadata_unavailable",
        "unsupported_claim",
        "high_risk_inference",
        "citation_precision",
        "vague_strong_word",
    }
    items = []
    for index, finding in enumerate(result.get("rewrite_contexts", []), start=1):
        if finding.get("category") not in semantic_categories:
            continue
        items.append(
            {
                "id": f"L2-{len(items) + 1:03d}",
                "source_finding_index": index,
                "category": finding.get("category", ""),
                "severity": finding.get("severity", ""),
                "l1_message": finding.get("message", ""),
                "context": finding.get("context", ""),
                "rewrite_hint": finding.get("rewrite_hint", ""),
                "semantic_questions": [
                    "citation 是否真的支撐該主張？",
                    "推論語氣是否超過來源證據？",
                    "是否需要改成保守措辭、補 citation、或刪除？",
                    "若 metadata 抓取失敗，是否能從上下文判斷需重新查官方來源？",
                ],
                "model_output_schema": {
                    "evidence_status": "supported | partially_supported | unsupported | unverifiable",
                    "inference_risk": "low | medium | high",
                    "action": "keep | rewrite | add_citation | replace_source | delete",
                    "reason": "中文短理由，不計算分數",
                    "rewrite_instruction": "中文改寫指令",
                },
            }
        )
        if len(items) >= max_items:
            break

    return {
        "packet_type": "semantic_audit_l2",
        "language": "zh-TW",
        "audit_summary": {
            "passed": result.get("passed"),
            "final_score": result.get("final_score"),
            "threshold": result.get("threshold"),
            "scores": result.get("scores", {}),
            "counts": result.get("counts", {}),
            "note": "以上分數與計數由 L1 deterministic code 產生；L2 模型不得重算、覆寫或心算分數。",
        },
        "semantic_review_instructions": [
            "只針對 items 逐條做語義審核，不重算分數。",
            "必須用繁體中文輸出。",
            "檢查 citation 是否語義支撐 context 中的主張。",
            "檢查是否有過度推論、因果跳躍、強詞或未明確限制適用範圍。",
            "若 L1 提供 citation metadata，優先使用 metadata/context 判斷來源是否對得上。",
            "若資料不足，標記 unverifiable，並要求回到官方論文、DOI、arXiv 或出版頁查證。",
            "輸出只能是審核意見與重寫指令，不得給新的 L1 分數。",
        ],
        "citation_metadata": {
            "mismatches": deterministic.get("citation_metadata_mismatches", []),
            "fetch_errors": deterministic.get("citation_metadata_fetch_errors", []),
        },
        "items": items,
    }


def write_semantic_packet(path: Path, result: dict) -> Path:
    packet_path = path.with_name(path.stem + ".semantic_audit_packet.json")
    packet_path.write_text(json.dumps(build_semantic_packet(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return packet_path


def write_rewrite_packet(path: Path, result: dict) -> Path:
    packet_path = path.with_name(path.stem + ".audit_rewrite_contexts.json")
    packet_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return packet_path


def append_audit(path: Path, result: dict) -> None:
    text = read_text(path)
    marker = "\n## Evidence Fidelity Audit\n"
    if marker in text:
        text = text.split(marker)[0].rstrip() + "\n"
    path.write_text(text.rstrip() + "\n" + audit_markdown(result), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a paper report before final delivery.")
    parser.add_argument("report", type=Path, help="Markdown report path")
    parser.add_argument("--source", type=Path, help="Optional source text extracted from paper")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--json-out", type=Path, help="Write audit JSON to this path")
    parser.add_argument("--check-links", action="store_true", help="Check URL reachability with HTTP requests")
    parser.add_argument(
        "--no-verify-citation-metadata",
        action="store_true",
        help="Disable default URL metadata verification for offline runs",
    )
    parser.add_argument("--write", action="store_true", help="Append audit score to report only when passed")
    parser.add_argument(
        "--rewrite-packet",
        action="store_true",
        help="Write .audit_rewrite_contexts.json when failed",
    )
    parser.add_argument(
        "--semantic-packet",
        action="store_true",
        help="Write .semantic_audit_packet.json for L2 model semantic review",
    )
    parser.add_argument(
        "--fail-on-audit-fail",
        action="store_true",
        help="Return exit code 2 when the report fails. Default is to return 0 after a complete audit.",
    )
    args = parser.parse_args()

    report = read_text(args.report)
    source = read_text(args.source) if args.source else ""
    result = audit_report(
        report,
        source=source,
        threshold=args.threshold,
        check_links=args.check_links,
        verify_citation_metadata_enabled=not args.no_verify_citation_metadata,
    )

    if args.write and result["passed"]:
        append_audit(args.report, result)

    if args.rewrite_packet and not result["passed"]:
        result["rewrite_packet_path"] = str(write_rewrite_packet(args.report, result))

    if args.semantic_packet:
        result["semantic_packet_path"] = str(write_semantic_packet(args.report, result))

    if args.json_out:
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_audit_fail and not result["passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
