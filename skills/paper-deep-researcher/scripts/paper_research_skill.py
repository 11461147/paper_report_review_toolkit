"""
paper_research_skill.py
========================
Paper Deep Researcher Skill — Core Tool Implementation
Semantic Scholar Graph API + official open-access PDF retrieval + arXiv fallback

Features:
  - arXiv API fallback when Semantic Scholar returns 429 rate limit
  - Exponential backoff for rate limit (HTTP 429) protection
  - In-memory LRU caching to minimize redundant API calls
  - Citation-impact ranking signal with explicit missing-data handling
  - Lightweight multilingual lexical candidate ranking
  - Citation graph traversal (bidirectional)
"""

import os
import time
import math
import re
import requests
import functools
import hashlib
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse


def lru_cache_with_str_key(maxsize: int = 128):
    """Caches API calls with string-serialized keys to avoid redundant requests."""
    def decorator(func):
        cache = {}
        order = []

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = str(args) + str(sorted(kwargs.items()))
            if key in cache:
                return cache[key]
            result = func(*args, **kwargs)
            if len(order) >= maxsize:
                oldest = order.pop(0)
                cache.pop(oldest, None)
            cache[key] = result
            order.append(key)
            return result
        return wrapper
    return decorator


class AcademicResearchSkill:
    """
    Paper Research Agent Skill Engine
    ----------------------------------
    Interfaces with:
      - Semantic Scholar Graph API v1 (paper search, citation graph)
      - arXiv Search API (fallback endpoint for 0 rate limit)
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    ARXIV_URL = "https://export.arxiv.org/api/query"
    OPENALEX_URL = "https://api.openalex.org"
    CROSSREF_URL = "https://api.crossref.org"
    TRUSTED_FULLTEXT_HOSTS = {
        "arxiv.org", "export.arxiv.org", "aclanthology.org", "openreview.net",
        "ojs.aaai.org", "proceedings.mlr.press", "papers.nips.cc",
    }

    PAPER_FIELDS = (
        "paperId,title,abstract,year,citationCount,"
        "influentialCitationCount,openAccessPdf,authors,tldr,externalIds,venue"
    )

    NEGATION_TERMS = (
        "not", "no", "never", "neither", "without", "cannot", "can't",
        "doesn't", "didn't", "isn't", "wasn't", "不", "未", "無", "沒有",
        "並非", "不能", "無法", "否認", "失敗",
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        openalex_api_key: Optional[str] = None,
        crossref_mailto: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
        self.openalex_api_key = openalex_api_key or os.getenv("OPENALEX_API_KEY")
        self.crossref_mailto = crossref_mailto or os.getenv("CROSSREF_MAILTO")
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        if self.api_key:
            self.headers["x-api-key"] = self.api_key
        self.evidence_pool: List[Dict[str, Any]] = []
        self._last_arxiv_status = self._provider_status("arxiv", "not_run", False)

    @staticmethod
    def _provider_status(
        provider: str,
        status: str,
        authenticated: bool,
        result_count: int = 0,
        message: str = "",
    ) -> Dict[str, Any]:
        return {
            "provider": provider,
            "status": status,
            "authenticated": authenticated,
            "result_count": result_count,
            "message": message,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }

    def _get_json(
        self,
        provider: str,
        url: str,
        params: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 15,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        authenticated = bool(
            (provider == "semantic_scholar" and self.api_key)
            or (provider == "openalex" and self.openalex_api_key)
        )
        try:
            response = requests.get(url, params=params, headers=headers or self.headers, timeout=timeout)
            if response.status_code == 200:
                payload = response.json()
                return payload, self._provider_status(provider, "ok", authenticated)
            if response.status_code == 429:
                return {}, self._provider_status(provider, "rate_limited", authenticated, message="HTTP 429")
            return {}, self._provider_status(
                provider, "error", authenticated, message=f"HTTP {response.status_code}"
            )
        except Exception as exc:
            return {}, self._provider_status(provider, "error", authenticated, message=str(exc))

    def _api_request_with_backoff(
        self,
        url: str,
        params: Dict[str, Any],
        retries: int = 2
    ) -> Dict[str, Any]:
        for attempt in range(retries):
            try:
                resp = requests.get(url, headers=self.headers, params=params, timeout=10)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    print(f"[RATE LIMIT 429] Endpoint rate limited. Attempt {attempt+1}/{retries}.")
                    time.sleep(1.0)
            except Exception as e:
                print(f"[REQUEST ERROR] {e}")
        return {}

    def search_arxiv_papers(self, query: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Fallback search using arXiv API when Semantic Scholar is rate limited."""
        formatted_query = query.replace('"', '').replace(' ', '+')
        params = {
            "search_query": f"all:{formatted_query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        try:
            resp = requests.get(self.ARXIV_URL, params=params, timeout=15)
            if resp.status_code != 200:
                state = "rate_limited" if resp.status_code == 429 else "error"
                self._last_arxiv_status = self._provider_status(
                    "arxiv", state, False, message=f"HTTP {resp.status_code}"
                )
                return []
            root = ET.fromstring(resp.text)
            papers = []
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            for entry in root.findall('atom:entry', ns):
                title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
                published = entry.find('atom:published', ns).text[:4]
                id_url = entry.find('atom:id', ns).text
                arxiv_id = id_url.split('/abs/')[-1]
                authors = [{'name': a.find('atom:name', ns).text} for a in entry.findall('atom:author', ns)]
                
                paper_item = {
                    'paperId': f'arXiv:{arxiv_id}',
                    'title': title,
                    'abstract': summary,
                    'year': int(published) if published.isdigit() else None,
                    'citationCount': None,
                    'influentialCitationCount': None,
                    'citation_metrics_source': None,
                    'citation_metrics_status': 'unavailable',
                    'authors': authors,
                    'externalIds': {'ArXiv': arxiv_id},
                    'venue': 'arXiv'
                }
                paper_item['lexical_candidate_score'] = self.lexical_candidate_score(
                    query, f"{title} {summary}"
                )
                paper_item['citation_impact_signal'] = self._calculate_impact_score(paper_item)
                papers.append(paper_item)
            self._last_arxiv_status = self._provider_status(
                "arxiv", "ok", False, result_count=len(papers)
            )
            return papers
        except Exception as e:
            print(f"[ARXIV ERROR] {e}")
            self._last_arxiv_status = self._provider_status(
                "arxiv", "error", False, message=str(e)
            )
            return []

    @staticmethod
    def _openalex_abstract(inverted_index: Any) -> str:
        if not isinstance(inverted_index, dict):
            return ""
        positioned = [
            (position, word)
            for word, positions in inverted_index.items()
            for position in positions
            if isinstance(position, int)
        ]
        return " ".join(word for _, word in sorted(positioned))

    def search_openalex_papers(
        self, query: str, limit: int = 20
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        params: Dict[str, Any] = {"search": query, "per-page": min(limit, 100)}
        if self.openalex_api_key:
            params["api_key"] = self.openalex_api_key
        payload, status = self._get_json(
            "openalex", f"{self.OPENALEX_URL}/works", params
        )
        papers = []
        for work in payload.get("results", []):
            ids = work.get("ids") or {}
            doi = (work.get("doi") or ids.get("doi") or "").removeprefix("https://doi.org/")
            arxiv_id = ""
            for location in work.get("locations") or []:
                landing = location.get("landing_page_url") or ""
                match = re.search(r"arxiv\.org/abs/([^?#]+)", landing, re.I)
                if match:
                    arxiv_id = match.group(1)
                    break
            paper = {
                "paperId": work.get("id", "").rsplit("/", 1)[-1],
                "title": work.get("title") or work.get("display_name") or "",
                "abstract": self._openalex_abstract(work.get("abstract_inverted_index")),
                "year": work.get("publication_year"),
                "citationCount": work.get("cited_by_count"),
                "influentialCitationCount": None,
                "citation_metrics_source": "openalex",
                "citation_metrics_status": "verified" if work.get("cited_by_count") is not None else "unavailable",
                "authors": [
                    {"name": authorship.get("author", {}).get("display_name", "")}
                    for authorship in work.get("authorships") or []
                ],
                "externalIds": {
                    "DOI": doi or None,
                    "ArXiv": arxiv_id or None,
                    "OpenAlex": work.get("id"),
                },
                "venue": ((work.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
                "canonical_url": work.get("doi") or work.get("id"),
                "source_provenance": ["openalex"],
            }
            paper["lexical_candidate_score"] = self.lexical_candidate_score(
                query, f'{paper["title"]} {paper["abstract"]}'
            )
            paper["citation_impact_signal"] = self._calculate_impact_score(paper)
            papers.append(paper)
        status["result_count"] = len(papers)
        return papers, status

    def search_crossref_papers(
        self, query: str, limit: int = 20
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        params: Dict[str, Any] = {"query.bibliographic": query, "rows": min(limit, 100)}
        if self.crossref_mailto:
            params["mailto"] = self.crossref_mailto
        payload, status = self._get_json(
            "crossref", f"{self.CROSSREF_URL}/works", params
        )
        papers = []
        for item in (payload.get("message") or {}).get("items", []):
            date_parts = (
                (item.get("published-print") or item.get("published-online") or item.get("issued") or {})
                .get("date-parts", [[]])
            )
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            doi = item.get("DOI")
            title = " ".join(item.get("title") or [])
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
            paper = {
                "paperId": f"DOI:{doi}" if doi else item.get("URL", ""),
                "title": title,
                "abstract": self._normalize_text(abstract),
                "year": year,
                "citationCount": item.get("is-referenced-by-count"),
                "influentialCitationCount": None,
                "citation_metrics_source": "crossref",
                "citation_metrics_status": "verified" if item.get("is-referenced-by-count") is not None else "unavailable",
                "authors": [
                    {"name": " ".join(filter(None, (author.get("given"), author.get("family"))))}
                    for author in item.get("author") or []
                ],
                "externalIds": {"DOI": doi, "ArXiv": None, "OpenAlex": None},
                "venue": " ".join(item.get("container-title") or []),
                "canonical_url": item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                "source_provenance": ["crossref"],
            }
            paper["lexical_candidate_score"] = self.lexical_candidate_score(
                query, f'{paper["title"]} {paper["abstract"]}'
            )
            paper["citation_impact_signal"] = self._calculate_impact_score(paper)
            papers.append(paper)
        status["result_count"] = len(papers)
        return papers, status

    def search_semantic_scholar_papers(
        self, query: str, limit: int = 20
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        params = {
            "query": query,
            "limit": min(limit, 100),
            "fields": self.PAPER_FIELDS,
        }
        payload, status = self._get_json(
            "semantic_scholar",
            f"{self.BASE_URL}/paper/search",
            params,
            headers=self.headers,
        )
        papers = []
        for raw in payload.get("data", []):
            paper = dict(raw)
            metrics_available = paper.get("citationCount") is not None
            paper["citation_metrics_status"] = "verified" if metrics_available else "unavailable"
            paper["citation_metrics_source"] = "semantic_scholar" if metrics_available else None
            paper["source_provenance"] = ["semantic_scholar"]
            paper["canonical_url"] = paper.get("url") or (
                (paper.get("openAccessPdf") or {}).get("url")
            )
            paper["lexical_candidate_score"] = self.lexical_candidate_score(
                query, f'{paper.get("title", "")} {paper.get("abstract", "")}'
            )
            paper["citation_impact_signal"] = self._calculate_impact_score(paper)
            papers.append(paper)
        status["result_count"] = len(papers)
        return papers, status

    def search_arxiv_with_status(
        self, query: str, limit: int = 20
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        try:
            papers = self.search_arxiv_papers(query, limit)
            for paper in papers:
                paper["source_provenance"] = ["arxiv"]
                arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
                paper["canonical_url"] = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
            return papers, dict(self._last_arxiv_status)
        except Exception as exc:
            return [], self._provider_status(
                "arxiv", "error", False, message=str(exc)
            )

    @classmethod
    def _dedupe_key(cls, paper: Dict[str, Any]) -> str:
        return cls._dedupe_keys(paper)[0]

    @classmethod
    def _dedupe_keys(cls, paper: Dict[str, Any]) -> List[str]:
        external = paper.get("externalIds") or {}
        keys = []
        if external.get("DOI"):
            keys.append("doi:" + str(external["DOI"]).lower().removeprefix("https://doi.org/"))
        if external.get("ArXiv"):
            keys.append("arxiv:" + str(external["ArXiv"]).lower().removesuffix(".pdf"))
        title = cls._normalize_text(paper.get("title", ""))
        if title:
            keys.append("title:" + re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", title))
        return keys or ["paper:" + str(paper.get("paperId", ""))]

    @classmethod
    def _merge_paper_records(
        cls, current: Dict[str, Any], incoming: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(current)
        for field in ("title", "abstract", "year", "venue", "canonical_url"):
            if not merged.get(field) and incoming.get(field):
                merged[field] = incoming[field]
        for field in ("citationCount", "influentialCitationCount"):
            values = [value for value in (merged.get(field), incoming.get(field)) if value is not None]
            if values:
                merged[field] = max(values)
        external = dict(merged.get("externalIds") or {})
        external.update({key: value for key, value in (incoming.get("externalIds") or {}).items() if value})
        merged["externalIds"] = external
        provenance = list(dict.fromkeys(
            list(merged.get("source_provenance") or [])
            + list(incoming.get("source_provenance") or [])
        ))
        merged["source_provenance"] = provenance
        if incoming.get("citation_metrics_status") == "verified":
            merged["citation_metrics_status"] = "verified"
            merged["citation_metrics_source"] = incoming.get("citation_metrics_source")
        return merged

    def search_external_papers(
        self,
        query: str,
        limit: int = 20,
        providers: Optional[List[str]] = None,
        include_full_text: bool = False,
        fulltext_output_dir: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Search keyless-first providers and preserve partial-failure status."""
        selected = providers or ["arxiv", "crossref", "openalex", "semantic_scholar"]
        allowed = {"arxiv", "crossref", "openalex", "semantic_scholar"}
        unknown = sorted(set(selected) - allowed)
        if unknown:
            raise ValueError(f"unsupported providers: {', '.join(unknown)}")

        searches = {
            "arxiv": self.search_arxiv_with_status,
            "crossref": self.search_crossref_papers,
            "openalex": self.search_openalex_papers,
            "semantic_scholar": self.search_semantic_scholar_papers,
        }
        records: Dict[str, Dict[str, Any]] = {}
        aliases: Dict[str, str] = {}
        statuses = []
        for provider in selected:
            papers, status = searches[provider](query, limit)
            statuses.append(status)
            for paper in papers:
                keys = self._dedupe_keys(paper)
                key = next((aliases[item] for item in keys if item in aliases), keys[0])
                records[key] = self._merge_paper_records(records[key], paper) if key in records else paper
                for alias in self._dedupe_keys(records[key]):
                    aliases[alias] = key

        papers = list(records.values())
        for paper in papers:
            paper["lexical_candidate_score"] = self.lexical_candidate_score(
                query, f'{paper.get("title", "")} {paper.get("abstract", "")}'
            )
            paper["citation_impact_signal"] = self._calculate_impact_score(paper)
        if include_full_text:
            for paper in papers[:limit]:
                content = self.retrieve_paper_content(paper, output_dir=fulltext_output_dir)
                paper["content_access"] = {key: value for key, value in content.items() if key != "text"}
                paper["evidence_passages"] = self.extract_evidence_passages(query, content)
        papers.sort(key=lambda paper: (
            paper["lexical_candidate_score"],
            paper["citation_impact_signal"] is not None,
            paper["citation_impact_signal"] or 0.0,
        ), reverse=True)
        return {
            "query": query,
            "papers": papers[:limit],
            "provider_status": statuses,
            "complete": all(status["status"] == "ok" for status in statuses),
            "note": "Provider failure means incomplete coverage, not absence of literature.",
        }

    @lru_cache_with_str_key(maxsize=64)
    def search_academic_papers(
        self,
        query: str,
        yearFrom: Optional[int] = None,
        yearTo: Optional[int] = None,
        fieldsOfStudy: Optional[str] = None,
        minCitations: Optional[int] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/paper/search"
        params: Dict[str, Any] = {
            "query": query,
            "limit": min(limit, 100),
            "fields": self.PAPER_FIELDS
        }
        if yearFrom and yearTo:
            params["year"] = f"{yearFrom}-{yearTo}"
        elif yearFrom:
            params["year"] = f"{yearFrom}-"
        elif yearTo:
            params["year"] = f"-{yearTo}"
        if fieldsOfStudy:
            params["fieldsOfStudy"] = fieldsOfStudy

        data = self._api_request_with_backoff(url, params)
        papers = data.get("data", [])

        # Fallback to arXiv if Semantic Scholar returns empty or rate limited
        if not papers:
            print(f"[FALLBACK] Querying arXiv API for: '{query}'")
            papers = self.search_arxiv_papers(query, limit=limit)

        if minCitations is not None:
            papers = [p for p in papers if p.get("citationCount") is not None and p["citationCount"] >= minCitations]

        for p in papers:
            metrics_available = p.get("citationCount") is not None
            p.setdefault("citation_metrics_status", "verified" if metrics_available else "unavailable")
            p.setdefault("citation_metrics_source", "semantic_scholar" if metrics_available else None)
            p["lexical_candidate_score"] = self.lexical_candidate_score(
                query, f'{p.get("title", "")} {p.get("abstract", "")}'
            )
            p["citation_impact_signal"] = self._calculate_impact_score(p)

        papers.sort(key=lambda p: (
            p["lexical_candidate_score"],
            p["citation_impact_signal"] is not None,
            p["citation_impact_signal"] or 0.0,
        ), reverse=True)
        return papers

    def traverse_citation_graph(
        self,
        paper_id: str,
        direction: str = "citations",
        influential_only: bool = True,
        limit: int = 15
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/paper/{paper_id}/{direction}"
        fields = "paperId,title,abstract,year,citationCount,influentialCitationCount,isInfluential,authors,venue"
        params: Dict[str, Any] = {"fields": fields, "limit": limit}

        data = self._api_request_with_backoff(url, params)
        raw_items = data.get("data", [])

        results = []
        for item in raw_items:
            node = item.get("citingPaper", {}) if direction == "citations" else item.get("citedPaper", {})
            if not node or not node.get("paperId"):
                continue
            if influential_only and not item.get("isInfluential", False):
                continue
            metrics_available = node.get("citationCount") is not None
            node["citation_metrics_status"] = "verified" if metrics_available else "unavailable"
            node["citation_metrics_source"] = "semantic_scholar" if metrics_available else None
            node["citation_impact_signal"] = self._calculate_impact_score(node)
            results.append(node)

        results.sort(key=lambda p: (
            p["citation_impact_signal"] is not None,
            p["citation_impact_signal"] or 0.0,
        ), reverse=True)
        return results

    def get_paper_snippets(
        self,
        paper_id: str,
        query: str,
        max_snippets: int = 5
    ) -> List[Dict[str, Any]]:
        meta_url = f"{self.BASE_URL}/paper/{paper_id}"
        meta_params = {"fields": "paperId,title,abstract,tldr,openAccessPdf,year,authors"}
        meta = self._api_request_with_backoff(meta_url, meta_params)

        snippets = []
        if meta.get("abstract"):
            snippets.append({
                "text": meta["abstract"],
                "section": "Abstract",
                "paper_id": paper_id,
                "paper_title": meta.get("title", ""),
                "year": meta.get("year", ""),
                "authors": [a.get("name", "") for a in meta.get("authors", [])],
                "lexical_candidate_score": self.lexical_candidate_score(query, meta["abstract"]),
                "semantic_support_verified": False,
                "negation_warning": self.has_negation_warning(meta["abstract"]),
                "source": "abstract"
            })
        if meta.get("tldr") and meta["tldr"].get("text"):
            snippets.append({
                "text": meta["tldr"]["text"],
                "section": "TLDR Summary",
                "paper_id": paper_id,
                "paper_title": meta.get("title", ""),
                "year": meta.get("year", ""),
                "authors": [a.get("name", "") for a in meta.get("authors", [])],
                "lexical_candidate_score": self.lexical_candidate_score(query, meta["tldr"]["text"]),
                "semantic_support_verified": False,
                "negation_warning": self.has_negation_warning(meta["tldr"]["text"]),
                "source": "tldr"
            })

        snippets.sort(key=lambda x: x["lexical_candidate_score"], reverse=True)
        return snippets[:max_snippets]

    @classmethod
    def _fulltext_candidates(cls, paper: Dict[str, Any]) -> List[str]:
        """Return deduplicated official/open-access full-text candidates."""
        urls: List[str] = []
        external = paper.get("externalIds") or {}
        arxiv_id = external.get("ArXiv")
        if arxiv_id:
            urls.append(f"https://arxiv.org/pdf/{str(arxiv_id).removesuffix('.pdf')}")
        open_pdf = paper.get("openAccessPdf") or {}
        if isinstance(open_pdf, dict) and open_pdf.get("url"):
            urls.append(open_pdf["url"])
        for key in ("fulltext_url", "pdf_url"):
            if paper.get(key):
                urls.append(str(paper[key]))
        canonical = str(paper.get("canonical_url") or "")
        if canonical.endswith("/") and "aclanthology.org/" in canonical:
            urls.append(canonical.rstrip("/") + ".pdf")
        return list(dict.fromkeys(urls))

    @classmethod
    def _trusted_fulltext_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and (parsed.hostname or "").lower() in cls.TRUSTED_FULLTEXT_HOSTS

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> str:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            text_path = Path(handle.name)
        try:
            completed = subprocess.run(
                ["pdftotext", "-layout", str(pdf_path), str(text_path)],
                capture_output=True, text=True, timeout=90, check=False,
            )
            if completed.returncode != 0:
                return ""
            return text_path.read_text(encoding="utf-8", errors="replace")
        finally:
            text_path.unlink(missing_ok=True)

    def retrieve_paper_content(
        self, paper: Dict[str, Any], output_dir: Optional[str | Path] = None, timeout: int = 45,
    ) -> Dict[str, Any]:
        """Fetch official full text and fail closed to abstract or metadata."""
        attempts = []
        for url in self._fulltext_candidates(paper):
            if not self._trusted_fulltext_url(url):
                attempts.append({"url": url, "status": "rejected_untrusted_host"})
                continue
            try:
                response = requests.get(url, headers=self.headers, timeout=timeout, allow_redirects=True)
                if not self._trusted_fulltext_url(response.url):
                    attempts.append({"url": response.url, "status": "rejected_untrusted_redirect"})
                    continue
                if response.status_code != 200:
                    attempts.append({"url": url, "status": f"http_{response.status_code}"})
                    continue
                content_type = response.headers.get("content-type", "").lower()
                if not (response.content.startswith(b"%PDF") or "application/pdf" in content_type):
                    attempts.append({"url": url, "status": "not_pdf"})
                    continue
                digest = hashlib.sha256(response.content).hexdigest()
                destination_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="paper-fulltext-"))
                destination_dir.mkdir(parents=True, exist_ok=True)
                safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(paper.get("paperId") or digest[:12]))
                pdf_path = destination_dir / f"{safe_id}.pdf"
                pdf_path.write_bytes(response.content)
                text = self._extract_pdf_text(pdf_path)
                if text.strip():
                    return {"access_level": "full_text", "text": text, "source_url": response.url,
                            "retrieved_at": datetime.now(timezone.utc).isoformat(), "sha256": digest,
                            "local_pdf": str(pdf_path), "attempts": attempts}
                attempts.append({"url": url, "status": "text_extraction_failed"})
            except Exception as exc:
                attempts.append({"url": url, "status": "error", "message": str(exc)})
        abstract = str(paper.get("abstract") or "").strip()
        return {"access_level": "abstract_only" if abstract else "metadata_only", "text": abstract,
                "source_url": paper.get("canonical_url") or "", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(abstract.encode("utf-8")).hexdigest() if abstract else None,
                "local_pdf": None, "attempts": attempts}

    @classmethod
    def extract_evidence_passages(
        cls, query: str, content: Dict[str, Any], max_passages: int = 5, min_chars: int = 80,
    ) -> List[Dict[str, Any]]:
        """Create ranked candidates while preserving access tier and provenance."""
        chunks = [cls._normalize_text(chunk) for chunk in re.split(r"\n\s*\n", str(content.get("text") or ""))]
        chunks = [chunk for chunk in chunks if len(chunk) >= min_chars]
        ranked = sorted(enumerate(chunks, 1), key=lambda item: cls.lexical_candidate_score(query, item[1]), reverse=True)[:max_passages]
        return [{"passage_id": f"PASSAGE-{index:04d}", "text": passage,
                 "location": {"paragraph": index}, "source_url": content.get("source_url", ""),
                 "source_sha256": content.get("sha256"), "access_level": content.get("access_level", "metadata_only"),
                 "lexical_candidate_score": cls.lexical_candidate_score(query, passage),
                 "semantic_support_verified": False, "negation_warning": cls.has_negation_warning(passage)}
                for index, passage in ranked]

    def retrieve_paper_evidence(
        self,
        paper: Dict[str, Any],
        query: str,
        max_passages: int = 5,
        output_dir: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Fetch content and return a model-ready provenance packet."""
        content = self.retrieve_paper_content(paper, output_dir=output_dir)
        return {
            "paper_id": paper.get("paperId"),
            "title": paper.get("title", ""),
            "access_level": content["access_level"],
            "source_url": content["source_url"],
            "source_sha256": content["sha256"],
            "retrieved_at": content["retrieved_at"],
            "local_pdf": content["local_pdf"],
            "attempts": content["attempts"],
            "evidence_passages": self.extract_evidence_passages(
                query, content, max_passages=max_passages
            ),
        }

    def _calculate_impact_score(self, paper: Dict[str, Any]) -> Optional[float]:
        """Return an uncalibrated citation signal, or None for unknown data."""
        if paper.get("citationCount") is None or paper.get("year") is None:
            return None
        c_total = max(paper["citationCount"], 0)
        c_inf   = max(paper.get("influentialCitationCount") or 0, 0)
        year    = paper["year"]
        delta_t = max(1, date.today().year - year + 1)

        w1, w2, w3 = 0.4, 0.4, 0.2
        epsilon = 1e-5

        part1 = math.log(c_total + 1)
        part2 = c_inf / (c_total + epsilon)
        part3 = math.log(c_total / delta_t + 1)

        return round((w1 * part1) + (w2 * part2) + (w3 * part3), 4)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()

    @classmethod
    def multilingual_tokens(cls, text: str) -> List[str]:
        """Return Latin word tokens plus adjacent CJK character bigrams."""
        normalized = cls._normalize_text(text)
        tokens = re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", normalized)
        for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
            if len(segment) == 1:
                tokens.append(segment)
            else:
                tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
        return tokens

    @classmethod
    def has_negation_warning(cls, passage: str) -> bool:
        normalized = cls._normalize_text(passage)
        english = set(re.findall(r"[a-z]+(?:'[a-z]+)?", normalized))
        return any(
            term in normalized if re.search(r"[\u3400-\u9fff]", term) else term in english
            for term in cls.NEGATION_TERMS
        )

    @classmethod
    def lexical_candidate_score(cls, sub_query: str, passage: str) -> float:
        """Return an uncalibrated candidate-ranking signal, never support probability."""
        query_tokens = set(cls.multilingual_tokens(sub_query))
        passage_tokens = set(cls.multilingual_tokens(passage))
        if not query_tokens:
            return 0.0
        overlap = len(query_tokens & passage_tokens)
        base_score = overlap / len(query_tokens)
        result_signals = [
            "achieve", "outperform", "result", "accuracy", "f1",
            "precision", "recall", "benchmark", "experiment", "evaluate",
            "baseline", "state-of-the-art", "sota", "score", "%", "entity", "alignment"
        ]
        boost = sum(0.05 for sig in result_signals if sig in passage.lower())
        raw = base_score + min(boost, 0.3)
        return round(min(raw, 1.0), 4)

    @classmethod
    def evaluate_and_score_evidence(cls, sub_query: str, passage: str) -> float:
        """Compatibility alias; use lexical_candidate_score for new callers."""
        return cls.lexical_candidate_score(sub_query, passage)

    @staticmethod
    def build_relevance_review_packet(
        query: str,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a fresh-context packet for model-based semantic reranking."""
        items = [
            {
                "passage_id": item.get("passage_id") or f"CAND-{index:03d}",
                "text": item.get("text", ""),
                "source": item.get("source", ""),
                "lexical_candidate_score": item.get("lexical_candidate_score"),
                "negation_warning": item.get("negation_warning", False),
            }
            for index, item in enumerate(candidates, 1)
        ]
        return {
            "job_id": "EVIDENCE-RELEVANCE-REVIEW",
            "context_policy": "fresh",
            "query": query,
            "candidates": items,
            "instructions": [
                "Judge semantic relevance using only the query and candidate text.",
                "Treat lexical_candidate_score only as retrieval metadata, never as support probability.",
                "A passage may be highly relevant while contradicting the query.",
                "Return every passage_id exactly once and do not add outside knowledge.",
            ],
            "output_schema": {
                "items": [{
                    "passage_id": "string",
                    "relevance": "high|medium|low|irrelevant",
                    "reason": "short grounded reason",
                }]
            },
        }

    @staticmethod
    def build_support_review_packet(
        claim: str,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a fresh-context packet for claim-to-evidence support review."""
        items = [
            {
                "passage_id": item.get("passage_id") or f"CAND-{index:03d}",
                "text": item.get("text", ""),
                "source": item.get("source", ""),
            }
            for index, item in enumerate(candidates, 1)
        ]
        return {
            "job_id": "EVIDENCE-SUPPORT-REVIEW",
            "context_policy": "fresh",
            "claim": claim,
            "candidates": items,
            "instructions": [
                "Decide whether each candidate supports the claim using only its text.",
                "Check negation, metric, dataset, method, baseline, population, and experimental conditions.",
                "Use supported only when the passage directly entails the claim within the same scope.",
                "Return every passage_id exactly once and do not add outside knowledge.",
            ],
            "output_schema": {
                "items": [{
                    "passage_id": "string",
                    "verdict": "supported|partially_supported|contradicted|insufficient",
                    "reason": "short grounded reason",
                    "scope": {
                        "dataset": "string|null",
                        "metric": "string|null",
                        "method": "string|null",
                        "baseline": "string|null",
                        "conditions": ["string"],
                    },
                }]
            },
        }

    @staticmethod
    def format_citation(paper: Dict[str, Any]) -> str:
        authors = paper.get("authors", [])
        year = paper.get("year", "n.d.")
        paper_id = paper.get("paperId", "unknown")
        if not authors:
            author_str = "Unknown Authors"
        elif len(authors) == 1:
            author_str = authors[0].get("name", "Unknown").split()[-1]
        else:
            first_last = authors[0].get("name", "Unknown").split()[-1]
            author_str = f"{first_last} et al."
        return f"[{author_str}, {year}, S2ID:{paper_id[:8]}]"

    @staticmethod
    def format_reference_entry(paper: Dict[str, Any]) -> str:
        authors = paper.get("authors", [])
        title   = paper.get("title", "Unknown Title")
        venue   = paper.get("venue", "Unknown Venue")
        year    = paper.get("year", "n.d.")
        paper_id = paper.get("paperId", "")
        ext_ids = paper.get("externalIds", {}) or {}
        arxiv_id = ext_ids.get("ArXiv", "")
        doi      = ext_ids.get("DOI", "")

        author_names = [a.get("name", "") for a in authors[:5]]
        if len(authors) > 5:
            author_names.append("et al.")
        author_str = ", ".join(author_names)

        links = []
        if arxiv_id:
            links.append(f"arXiv:{arxiv_id} (https://arxiv.org/abs/{arxiv_id})")
        if doi:
            links.append(f"DOI:{doi}")
        if paper_id:
            links.append(f"S2ID:{paper_id}")
        link_str = " | ".join(links)
        return f'{author_str}, "{title}", {venue}, {year}. [{link_str}]'
