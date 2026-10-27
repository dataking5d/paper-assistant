#!/usr/bin/env python3
"""Paper Retriever — 文献自动检索与下载助手 MVP.

输入论文标题列表，自动检索元数据并下载开放获取 PDF。
"""

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pdfplumber
import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from tenacity import retry, stop_after_attempt, wait_exponential


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg: str, log_dir: str = "logs", level: str = "INFO") -> None:
    """Write timestamped log line to daily file and console."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [{level}] {msg}"
    print(line)
    os.makedirs(log_dir, exist_ok=True)
    date_tag = datetime.now().strftime("%Y-%m-%d")
    with open(os.path.join(log_dir, f"{date_tag}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_filename(title: str, max_len: int = 120) -> str:
    """Convert a paper title to a safe filename stem."""
    s = unicodedata.normalize("NFKD", title)
    s = re.sub(r"[^\w\s\-]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:max_len]


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"


# ---------------------------------------------------------------------------
# Rate Limiter — per-domain backoff with jitter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Track per-domain request timing with exponential backoff + jitter.

    Each API domain gets its own failure counter. Consecutive failures
    increase the minimum gap between requests; a success resets it.
    """

    def __init__(self, base_delay: float = 0.5):
        self._base_delay = base_delay
        self._last_request: dict[str, float] = {}
        self._failures: dict[str, int] = {}

    @staticmethod
    def _domain_of(url: str) -> str:
        m = re.search(r"https?://([^/]+)", url)
        return m.group(1) if m else url

    def wait(self, url: str) -> None:
        domain = self._domain_of(url)
        now = time.time()
        gap = self._base_delay * (1.5 ** self._failures.get(domain, 0))
        elapsed = now - self._last_request.get(domain, 0)
        if elapsed < gap:
            jitter = random.uniform(0, gap * 0.3)
            time.sleep(gap - elapsed + jitter)
        self._last_request[domain] = time.time()

    def record_success(self, url: str) -> None:
        domain = self._domain_of(url)
        self._failures[domain] = 0

    def record_failure(self, url: str) -> None:
        domain = self._domain_of(url)
        self._failures[domain] = self._failures.get(domain, 0) + 1

    def parse_retry_after(self, headers: dict) -> float | None:
        val = headers.get("Retry-After", "")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# PaperRetriever
# ---------------------------------------------------------------------------

class PaperRetriever:
    """Orchestrate metadata search and PDF download for a list of titles."""

    def __init__(self, input_path: str, output_dir: str = "papers", max_retries: int = 3, email: str = "", delay: float = 1.0):
        self.input_path = Path(input_path)
        self.output_dir = Path(output_dir)
        self.max_retries = max_retries
        self.email = email
        self.delay = delay
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.checkpoint_path = self.output_dir / "checkpoint.json"
        self.report_path = self.output_dir / "result.xlsx"
        self.pdf_dir = self.output_dir / "papers"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = str(self.output_dir / "logs")

        self.checkpoint: dict = {"done": {}, "results": []}
        self.rate_limiter = RateLimiter(base_delay=0.5)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "PaperRetriever/1.0 (mailto:research@example.com)"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        _log("Paper Retriever started", self.log_dir)
        titles = self._load_titles()
        _log(f"Loaded {len(titles)} unique titles", self.log_dir)
        self._load_checkpoint()

        for idx, title in enumerate(titles, 1):
            if self.checkpoint["done"].get(title) == "success":
                _log(f"[{idx}/{len(titles)}] SKIP (cached): {title[:80]}", self.log_dir)
                continue

            _log(f"[{idx}/{len(titles)}] Processing: {title[:80]}", self.log_dir)
            result = self._process_one(title)
            self.checkpoint["done"][title] = result["pdf_status"]
            self.checkpoint["results"].append(result)
            self._save_checkpoint()
            time.sleep(self.delay)  # polite pacing between papers

        self._write_report()
        success_count = sum(1 for r in self.checkpoint["results"] if r["pdf_status"] == "success")
        _log(
            f"Done. {success_count}/{len(self.checkpoint['results'])} downloaded. "
            f"Report: {self.report_path}",
            self.log_dir,
        )

    def check(self, checklist_path: str | None = None) -> dict:
        """Metadata-only scan: check which papers can be auto-downloaded.

        Returns a dict with keys 'auto', 'manual', 'missing' — each a list of
        result dicts.  Writes a markdown checklist if ``checklist_path`` is given.
        """
        titles = self._load_titles()
        _log(f"Check mode: scanning {len(titles)} titles (metadata only, no downloads)", self.log_dir)

        auto: list[dict] = []
        manual: list[dict] = []
        missing: list[dict] = []

        for idx, title in enumerate(titles, 1):
            _log(f"[{idx}/{len(titles)}] Checking: {title[:80]}", self.log_dir)

            # Step 1 — metadata
            meta = self._find_metadata(title)
            if not meta or not meta.get("doi"):
                missing.append({
                    "idx": idx, "title": title,
                    "is_chinese": bool(re.search(r'[一-鿿]', title)),
                })
                time.sleep(self.delay)
                continue

            # Step 2 — PDF source (don't download)
            pdf_url, pdf_source = self._find_pdf_url(meta, title)
            if pdf_url:
                auto.append({
                    "idx": idx, "title": title,
                    "doi": meta["doi"], "matched_title": meta.get("matched_title", ""),
                    "source": pdf_source, "pdf_url": pdf_url,
                })
            else:
                manual.append({
                    "idx": idx, "title": title,
                    "doi": meta["doi"], "matched_title": meta.get("matched_title", ""),
                })

            time.sleep(self.delay)

        # Write markdown checklist
        if checklist_path is None:
            checklist_path = str(self.output_dir / "missing_checklist.md")
        self._write_checklist(auto, manual, missing, checklist_path)

        # Print summary
        print(f"\n{'='*60}")
        print(f"文献获取状态检查完成")
        print(f"{'='*60}")
        print(f"  可自动下载: {len(auto)} 篇")
        print(f"  需手动提供: {len(manual)} 篇")
        print(f"  未找到元数据: {len(missing)} 篇")
        print(f"  清单已保存: {checklist_path}")

        return {"auto": auto, "manual": manual, "missing": missing}

    def _write_checklist(self, auto: list, manual: list, missing: list, path: str) -> None:
        """Write actionable markdown checklist for missing papers."""
        lines = [
            "# 文献获取状态清单",
            "",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"总计: {len(auto) + len(manual) + len(missing)} 篇",
            "",
            f"## 已自动获取 ({len(auto)} 篇)",
            "",
        ]
        if auto:
            for r in auto:
                lines.append(f"- [{r['idx']}] **{r['title']}**  ")
                lines.append(f"  DOI: [{r['doi']}](https://doi.org/{r['doi']}) | 来源: {r['source']}")
        else:
            lines.append("（无）")

        lines += [
            "",
            f"## 需手动提供 PDF ({len(manual)} 篇)",
            "",
            "> 以下文献已找到元数据但无开放获取版本，请通过机构订阅、Sci-Hub 或馆际互借获取 PDF，放入 `ref_pdfs/` 目录。",
            "",
        ]
        if manual:
            for r in manual:
                lines.append(f"- [{r['idx']}] **{r['title']}**  ")
                lines.append(f"  DOI: [{r['doi']}](https://doi.org/{r['doi']})")
                lines.append(f"  匹配标题: {r['matched_title'][:80]}")
        else:
            lines.append("（无）")

        lines += [
            "",
            f"## 未找到元数据 ({len(missing)} 篇)",
            "",
            "> 以下文献在 Crossref/OpenAlex/arXiv 中均未找到，可能是中文文献或信息不完整。请手动检索。",
            "",
        ]
        if missing:
            for r in missing:
                lines.append(f"- [{r['idx']}] **{r['title']}**  ")
                if r.get("is_chinese"):
                    q = quote(r['title'])
                    lines.append(f"  知网: <https://kns.cnki.net/kns8s/search?classid=YSTT4HG0&kw={q}>")
        else:
            lines.append("（无）")

        lines += [
            "",
            "## 下一步",
            "",
            "1. 将手动获取的 PDF 放入 `ref_pdfs/` 目录",
            "2. 运行 citation-assistant 的 `kb` 命令构建知识库",
            "3. 运行 `full` 命令完成引用生成与插入",
        ]

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Input parsing
    # ------------------------------------------------------------------

    def _load_titles(self) -> list[str]:
        ext = self.input_path.suffix.lower()
        titles: list[str]
        if ext == ".txt":
            titles = self._load_txt()
            # Auto-detect GB/T 7714 format
            if titles and re.match(r"^\[\d+\]", titles[0]):
                titles = self._parse_gbt7714_titles(titles)
        elif ext == ".csv":
            titles = self._load_csv()
        elif ext in (".xlsx", ".xls"):
            titles = self._load_xlsx()
        else:
            raise ValueError(f"Unsupported input format: {ext}")

        seen = set()
        unique = []
        for t in titles:
            key = t.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(t.strip())
        return unique

    def _load_txt(self) -> list[str]:
        with open(self.input_path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    def _load_csv(self) -> list[str]:
        titles = []
        with open(self.input_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = [c.lower() for c in (reader.fieldnames or [])]
            title_col = next((c for c in cols if "title" in c or "标题" in c), cols[0])
            for row in reader:
                if val := row.get(title_col, "").strip():
                    titles.append(val)
        return titles

    def _load_xlsx(self) -> list[str]:
        from openpyxl import load_workbook

        wb = load_workbook(self.input_path, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        header = [str(c).lower().strip() if c else "" for c in rows[0]]
        title_col = next((i for i, h in enumerate(header) if "title" in h or "标题" in h), 0)
        titles = [str(r[title_col]).strip() for r in rows[1:] if len(r) > title_col and r[title_col]]
        wb.close()
        return titles

    # ------------------------------------------------------------------
    # Metadata search (3-API fallback)
    # ------------------------------------------------------------------

    @staticmethod
    def _title_similarity(query: str, candidate: str) -> float:
        """Jaccard similarity of normalized word sets. 1.0 = identical."""
        def tokens(s: str) -> set[str]:
            return set(re.sub(r"[^\w\s]", "", s.lower()).split())
        q = tokens(query)
        c = tokens(candidate)
        if not q or not c:
            return 0.0
        return len(q & c) / len(q | c)

    @staticmethod
    def _score_result(title_sim: float, cited_by: int) -> float:
        """Composite score: title similarity dominates, citations break ties."""
        return title_sim + math.log(1 + cited_by) * 0.05

    def _find_metadata(self, title: str) -> dict:
        """Try OpenAlex → Crossref → arXiv → Semantic Scholar. Return metadata dict."""
        for searcher in [
            self._search_openalex,
            self._search_crossref,
            self._search_arxiv,
            self._search_semantic_scholar,
        ]:
            try:
                meta = searcher(title)
                if meta and meta.get("doi"):
                    sim = self._title_similarity(title, meta.get("matched_title", ""))
                    if sim < 0.4:
                        _log(f"  Low title match ({searcher.__name__} sim={sim:.2f}), trying next API", self.log_dir, "WARN")
                        time.sleep(0.5)
                        continue
                    if self._is_suspicious_doi(meta["doi"]):
                        _log(f"  Suspicious DOI prefix ({meta['doi'][:20]}...), trying next API", self.log_dir, "WARN")
                        time.sleep(0.5)
                        continue
                    return meta
            except Exception as exc:
                _log(f"  API error ({searcher.__name__}): {exc}", self.log_dir, "WARN")
                if "429" in str(exc):
                    _log("  Rate-limited, waiting 3s...", self.log_dir)
                    time.sleep(3)
            time.sleep(0.5)
        return {}

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=15), reraise=True)
    def _search_openalex(self, title: str) -> dict | None:
        url = f"https://api.openalex.org/works?search={quote(title)}&per_page=5"
        self.rate_limiter.wait(url)
        resp = self.session.get(url, timeout=20)
        if resp.status_code in (429, 503):
            retry_after = self.rate_limiter.parse_retry_after(resp.headers)
            wait_s = retry_after or 5
            self.rate_limiter.record_failure(url)
            _log(f"  OpenAlex {resp.status_code}, waiting {wait_s:.0f}s", self.log_dir, "WARN")
            time.sleep(wait_s)
            resp.raise_for_status()
        resp.raise_for_status()
        self.rate_limiter.record_success(url)
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        best, best_score = None, -1.0
        for r in results:
            sim = self._title_similarity(title, r.get("title", ""))
            cited = r.get("cited_by_count", 0) or 0
            score = self._score_result(sim, cited)
            if score > best_score:
                best_score = score
                best = r

        if best is None:
            return None
        doi = (best.get("doi") or "").replace("https://doi.org/", "")
        oa_loc = best.get("best_oa_location") or {}
        oa = best.get("open_access", {})
        return {
            "doi": doi,
            "matched_title": best.get("title", ""),
            "authors": ", ".join(
                a.get("author", {}).get("display_name", "")
                for a in (best.get("authorships") or [])[:5]
            ),
            "year": str(best.get("publication_date", ""))[:4],
            "source_db": "OpenAlex",
            "oa_pdf_url": oa_loc.get("pdf_url") if oa_loc.get("is_oa") else None,
            "oa_status": oa.get("oa_status", ""),
            "arxiv_id": self._extract_arxiv_id(best),
            "raw": best,
        }

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=15), reraise=True)
    def _search_crossref(self, title: str) -> dict | None:
        url = f"https://api.crossref.org/works?rows=5&query.title={quote(title)}"
        self.rate_limiter.wait(url)
        resp = self.session.get(url, timeout=20)
        if resp.status_code in (429, 503):
            retry_after = self.rate_limiter.parse_retry_after(resp.headers)
            wait_s = retry_after or 5
            self.rate_limiter.record_failure(url)
            _log(f"  Crossref {resp.status_code}, waiting {wait_s:.0f}s", self.log_dir, "WARN")
            time.sleep(wait_s)
            resp.raise_for_status()
        resp.raise_for_status()
        self.rate_limiter.record_success(url)
        data = resp.json()
        items = data.get("message", {}).get("items", [])
        if not items:
            return None

        best, best_score = None, -1.0
        for r in items:
            r_title = (r.get("title") or [""])[0]
            sim = self._title_similarity(title, r_title)
            cited = r.get("is-referenced-by-count", 0) or 0
            score = self._score_result(sim, cited)
            if score > best_score:
                best_score = score
                best = r

        if best is None:
            return None
        authors = best.get("author", [])
        date_parts = (
            best.get("published-print", {}).get("date-parts", [[None]])[0]
            or best.get("created", {}).get("date-parts", [[None]])[0]
        )
        return {
            "doi": best.get("DOI", ""),
            "matched_title": (best.get("title") or [""])[0],
            "authors": ", ".join(
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (authors or [])[:5]
            ),
            "year": str(date_parts[0]) if date_parts and date_parts[0] else "",
            "source_db": "Crossref",
            "oa_pdf_url": None,
            "oa_status": "",
            "arxiv_id": "",
            "raw": best,
        }

    # arXiv API — most reliable for CS papers, avoids DOI pollution
    _SUSPICIOUS_DOI_PREFIXES = {"10.65215"}  # known preprint/plagiarism DOI prefixes

    @staticmethod
    def _is_suspicious_doi(doi: str) -> bool:
        """Check if DOI prefix is from a known unreliable source."""
        prefix = doi.split("/")[0] if "/" in doi else ""
        return prefix in PaperRetriever._SUSPICIOUS_DOI_PREFIXES

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=15), reraise=True)
    def _search_arxiv(self, title: str) -> dict | None:
        """Search arXiv API. Best for CS/ML papers."""
        try:
            url = (
                "https://export.arxiv.org/api/query"
                f"?search_query=ti:%22{quote(title)}%22&max_results=5"
            )
            self.rate_limiter.wait(url)
            resp = self.session.get(url, timeout=20)
            resp.raise_for_status()
            self.rate_limiter.record_success(url)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }
            root = ET.fromstring(resp.text)
            entries = root.findall("atom:entry", ns)
            if not entries:
                return None

            best, best_score = None, -1.0
            for entry in entries:
                entry_title_el = entry.find("atom:title", ns)
                entry_title = entry_title_el.text.strip().replace("\n", " ") if entry_title_el is not None else ""
                sim = self._title_similarity(title, entry_title)
                if sim > best_score:
                    best_score = sim
                    best = entry

            if best is None or best_score < 0.4:
                return None

            entry_title_el = best.find("atom:title", ns)
            matched_title = entry_title_el.text.strip().replace("\n", " ") if entry_title_el is not None else ""
            arxiv_id = best.find("atom:id", ns).text.split("/")[-1]
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)
            published = best.find("atom:published", ns)
            year = published.text[:4] if published is not None else ""
            authors_els = best.findall("atom:author", ns)
            authors = ", ".join(
                a.find("atom:name", ns).text
                for a in authors_els[:5]
                if a.find("atom:name", ns) is not None
            )
            # Extract DOI if present
            doi = ""
            for link in best.findall("atom:link", ns):
                if link.get("title") == "doi":
                    doi = link.get("href", "").replace("http://dx.doi.org/", "")
            return {
                "doi": doi or f"10.48550/arXiv.{arxiv_id}",
                "matched_title": matched_title,
                "authors": authors,
                "year": year,
                "source_db": "arXiv",
                "oa_pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                "oa_status": "gold",
                "arxiv_id": arxiv_id,
                "raw": {},
            }
        except Exception:
            return None

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=15), reraise=True)
    def _search_semantic_scholar(self, title: str) -> dict | None:
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quote(title)}&limit=5"
            "&fields=title,authors,year,externalIds,openAccessPdf,citationCount"
        )
        self.rate_limiter.wait(url)
        resp = self.session.get(url, timeout=20)
        if resp.status_code in (429, 503):
            retry_after = self.rate_limiter.parse_retry_after(resp.headers)
            wait_s = retry_after or 5
            self.rate_limiter.record_failure(url)
            _log(f"  Semantic Scholar {resp.status_code}, waiting {wait_s:.0f}s", self.log_dir, "WARN")
            time.sleep(wait_s)
            resp.raise_for_status()
        resp.raise_for_status()
        self.rate_limiter.record_success(url)
        data = resp.json()
        items = data.get("data", [])
        if not items:
            return None

        best, best_score = None, -1.0
        for r in items:
            sim = self._title_similarity(title, r.get("title", ""))
            cited = r.get("citationCount", 0) or 0
            score = self._score_result(sim, cited)
            if score > best_score:
                best_score = score
                best = r

        if best is None:
            return None
        ext = best.get("externalIds") or {}
        oa = best.get("openAccessPdf") or {}
        return {
            "doi": ext.get("DOI", ""),
            "matched_title": best.get("title", ""),
            "authors": ", ".join(
                a.get("name", "") for a in (best.get("authors") or [])[:5]
            ),
            "year": str(best.get("year") or ""),
            "source_db": "Semantic Scholar",
            "oa_pdf_url": oa.get("url"),
            "oa_status": "",
            "arxiv_id": ext.get("ArXiv", ""),
            "raw": r,
        }

    @staticmethod
    def _extract_arxiv_id(openalex_result: dict) -> str:
        """Extract arXiv ID from OpenAlex result if present."""
        for loc in openalex_result.get("locations", []):
            landing = (loc.get("landing_page_url") or "").lower()
            if "arxiv.org" in landing:
                m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-]+)", landing)
                if m:
                    return m.group(1)
        # Also check primary location
        pl = openalex_result.get("primary_location", {}) or {}
        landing = (pl.get("landing_page_url") or "").lower()
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-]+)", landing)
        return m.group(1) if m else ""

    # ------------------------------------------------------------------
    # PDF URL discovery (5-source legal cascade)
    # ------------------------------------------------------------------

    def _search_unpaywall(self, doi: str) -> str | None:
        """Query Unpaywall for legal OA PDF locations. Covers all disciplines."""
        if not self.email:
            _log("  Unpaywall skipped (no email set)", self.log_dir)
            return None
        try:
            url = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(self.email)}"
            resp = self.session.get(url, timeout=15)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            best = data.get("best_oa_location") or {}
            if best.get("url_for_pdf"):
                return best["url_for_pdf"]
            for loc in data.get("oa_locations") or []:
                if loc.get("url_for_pdf"):
                    return loc["url_for_pdf"]
        except Exception as exc:
            _log(f"  Unpaywall lookup failed: {exc}", self.log_dir, "WARN")
        return None

    def _find_pdf_url(self, meta: dict, title: str) -> tuple[str | None, str]:
        """Return (pdf_url, source_name). Legal-first cascade: Unpaywall→arXiv→OA→PMC."""
        doi = meta.get("doi", "")

        # 1. Unpaywall — aggregates all legal OA sources across all disciplines
        if doi:
            upw_url = self._search_unpaywall(doi)
            if upw_url:
                return upw_url, "Unpaywall"

        # 2. arXiv — most reliable for CS/physics/math
        arxiv_id = meta.get("arxiv_id", "")
        if arxiv_id:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf", "arXiv"

        # 3. OpenAlex / Semantic Scholar OA PDF
        if meta.get("oa_pdf_url"):
            return meta["oa_pdf_url"], "OA Repository"

        # 4. PubMed Central — biomedical OA
        if doi:
            pmc_url = self._find_pmc_pdf(doi)
            if pmc_url:
                return pmc_url, "PubMed Central"

        return None, "none"

    def _find_pmc_pdf(self, doi: str) -> str | None:
        """Query PubMed Central OA service for a PDF link."""
        try:
            url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={quote(doi)}"
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            # Parse XML response for pdf links
            for m in re.finditer(r'format="pdf".*?href="(https?://[^"]+\.pdf)"', resp.text):
                return m.group(1)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _download_pdf(self, url: str, title: str) -> str | None:
        """Download PDF. Returns local path on success, None on failure."""
        if not url:
            return None

        filename = _sanitize_filename(title) + ".pdf"
        filepath = self.pdf_dir / filename

        # Skip if already downloaded
        if filepath.exists() and filepath.stat().st_size > 0:
            _log(f"  PDF already exists: {filepath.name}", self.log_dir)
            return str(filepath)

        return self._do_download(url, filepath)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _do_download(self, url: str, filepath: Path) -> str | None:
        """Core download with retry. Tenacity wraps this with exponential backoff."""
        headers = {}
        if "doi.org" in url:
            headers["Accept"] = "application/pdf"

        resp = self.session.get(url, headers=headers, timeout=60, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")

        # Read first 4 bytes to verify PDF magic, then stream the rest
        first_chunk = b""
        try:
            first_chunk = next(resp.iter_content(chunk_size=4), b"")
        except StopIteration:
            pass

        is_pdf = first_chunk == b"%PDF" or "application/pdf" in content_type
        if not is_pdf:
            _log(f"  Not a PDF: content-type={content_type}", self.log_dir, "WARN")
            return None

        with open(filepath, "wb") as f:
            f.write(first_chunk)
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_bytes = filepath.stat().st_size
        if size_bytes == 0:
            filepath.unlink()
            _log(f"  Downloaded file is empty, discarded", self.log_dir, "WARN")
            return None

        size_kb = size_bytes / 1024
        _log(f"  Downloaded: {filepath.name} ({size_kb:.0f} KB)", self.log_dir)
        return str(filepath)

    # ------------------------------------------------------------------
    # Per-paper pipeline
    # ------------------------------------------------------------------

    def _process_one(self, title: str) -> dict:
        """Run the full pipeline for a single title."""
        result = {
            "original_title": title,
            "matched_title": "",
            "doi": "",
            "authors": "",
            "year": "",
            "source_db": "",
            "pdf_status": "failed",
            "pdf_path": "",
            "pdf_source": "",
            "error": "",
        }

        # Step 1: metadata search
        meta = self._find_metadata(title)
        if not meta or not meta.get("doi"):
            result["error"] = "No DOI found via any API"
            _log(f"  FAIL: {result['error']}", self.log_dir, "WARN")
            return result

        result.update({
            "matched_title": meta.get("matched_title", ""),
            "doi": meta.get("doi", ""),
            "authors": meta.get("authors", ""),
            "year": meta.get("year", ""),
            "source_db": meta.get("source_db", ""),
        })
        _log(f"  DOI: {result['doi']} ({result['source_db']})", self.log_dir)

        # Step 2: find PDF URL
        pdf_url, pdf_source = self._find_pdf_url(meta, title)
        result["pdf_source"] = pdf_source
        if not pdf_url:
            result["error"] = "No OA PDF source found"
            result["pdf_status"] = "no_source"
            _log(f"  FAIL: {result['error']}", self.log_dir, "WARN")
            return result

        _log(f"  PDF source: {pdf_source}", self.log_dir)

        # Step 3: download
        try:
            local_path = self._download_pdf(pdf_url, title)
            if local_path:
                result["pdf_path"] = local_path
                result["pdf_status"] = "success"
            else:
                result["error"] = "Download returned no content"
                result["pdf_status"] = "download_failed"
                _log(f"  FAIL: {result['error']}", self.log_dir, "WARN")
        except Exception as exc:
            result["error"] = f"Download error: {exc}"
            result["pdf_status"] = "download_failed"
            _log(f"  FAIL: {result['error']}", self.log_dir, "WARN")

        return result

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> None:
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, encoding="utf-8") as f:
                    self.checkpoint = json.load(f)
                _log(
                    f"Resumed checkpoint: {len(self.checkpoint['done'])} done, "
                    f"{len(self.checkpoint['results'])} results",
                    self.log_dir,
                )
            except Exception:
                _log("Corrupt checkpoint, starting fresh", self.log_dir, "WARN")

    def _save_checkpoint(self) -> None:
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(self.checkpoint, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _write_report(self) -> None:
        wb = Workbook()
        ws = wb.active
        ws.title = "检索结果"

        headers = [
            "#", "原始标题", "匹配标题", "DOI", "作者", "年份",
            "来源数据库", "PDF状态", "PDF来源", "PDF路径", "失败原因",
        ]
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        wrap = Alignment(wrap_text=True, vertical="top")

        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = wrap

        for i, r in enumerate(self.checkpoint["results"], 1):
            row = [
                i,
                r.get("original_title", ""),
                r.get("matched_title", ""),
                r.get("doi", ""),
                r.get("authors", ""),
                r.get("year", ""),
                r.get("source_db", ""),
                r.get("pdf_status", ""),
                r.get("pdf_source", ""),
                r.get("pdf_path", ""),
                r.get("error", ""),
            ]
            for col, val in enumerate(row, 1):
                cell = ws.cell(row=i + 1, column=col, value=val)
                cell.alignment = wrap

        # Auto-width (capped)
        for col_cells in ws.columns:
            max_len = max(
                min(len(str(cell.value or "")), 60) for cell in col_cells
            )
            ws.column_dimensions[col_cells[0].column_letter].width = max_len + 2

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        wb.save(self.report_path)
        _log(f"Report written: {self.report_path}", self.log_dir)


# ---------------------------------------------------------------------------
# PDF Reference Parser (GB/T 7714 format)
# ---------------------------------------------------------------------------

def parse_references_from_pdf(pdf_path: str) -> list[dict]:
    """Extract structured references from a PDF in GB/T 7714 format.

    Handles both Chinese (Author1,Author2.Title[J/D/M]) and English
    (Author1;Author2.Title[J]) formats. Returns list of dicts with keys:
    title, authors, year, journal, pub_type, doi.
    """
    refs = _extract_ref_text(pdf_path)
    parsed = []
    for ref_text in refs:
        try:
            entry = _parse_single_ref(ref_text)
            if entry and entry.get("title"):
                parsed.append(entry)
        except Exception:
            pass
    return parsed


def _extract_ref_text(pdf_path: str) -> list[str]:
    """Extract raw reference texts from PDF, joining wrapped lines and
    restoring mid-word breaks (e.g. 'Tec\\nhnological' → 'Technological')."""
    with pdfplumber.open(pdf_path) as pdf:
        raw_lines = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                raw_lines.extend(text.split("\n"))

    # Merge: lines starting with [N] are new references
    refs = []
    current = None
    for line in raw_lines:
        line = line.strip()
        if not line or line in ("参考文献", "References"):
            continue
        if re.match(r"^\[\d+\]", line):
            if current:
                refs.append(current)
            current = line
        elif current is not None:
            # Join with a null-char marker to distinguish line-break from space
            current += "\x00" + line
    if current:
        refs.append(current)

    # Fix mid-word breaks at line boundaries (marked by \x00)
    # Remove \x00 when both sides are letters (broken word across lines)
    # Replace \x00 with space otherwise (genuine word boundary)
    fixed = []
    for r in refs:
        r = re.sub(r"(\w)\x00(\w)", r"\1\2", r)   # join broken words
        r = r.replace("\x00", " ")                 # restore real spaces
        r = re.sub(r"\s+", " ", r).strip()         # collapse whitespace
        fixed.append(r)
    return fixed


def _parse_single_ref(raw: str) -> dict | None:
    """Parse a single GB/T 7714 reference line into structured fields."""
    # Strip [N] prefix
    raw = re.sub(r"^\[\d+\]\s*", "", raw).strip()

    pub_type = ""
    pub_match = re.search(r"\[([JDCMP])\]", raw)
    if pub_match:
        pub_type = pub_match.group(1)
        before_pub = raw[:pub_match.start()].strip()
        after_pub = raw[pub_match.end():].strip().rstrip(".")
    else:
        before_pub = raw
        after_pub = ""

    # Extract year from journal info
    year = ""
    year_match = re.search(r"(\d{4})", after_pub)
    if year_match:
        year = year_match.group(1)

    # Extract DOI if present
    doi = ""
    doi_match = re.search(r"10\.\d{4,}/[^\s]+", raw)
    if doi_match:
        doi = doi_match.group(0).rstrip(".")

    # Split authors and title
    # Normalize: fix PDF artifacts like "Kong C .Title" → "Kong C. Title"
    before_pub = re.sub(r"([A-Z])\s+\.", r"\1.", before_pub)
    before_pub = re.sub(r"\s+", " ", before_pub)

    authors = ""
    title = ""
    # Find author-title boundary
    dot_idx = before_pub.find(".")
    if dot_idx > 0 and dot_idx < 80:
        candidate = before_pub[:dot_idx].strip()
        has_chinese = bool(re.search(r"[一-鿿]", candidate))
        has_sep = bool(re.search(r"[,;]", candidate))
        is_english_name = bool(re.match(
            r"^[A-Z][a-z]*(?:\s+[A-Z]\.?)?(?:\s*[,;]\s*[A-Z][a-z]*(?:\s+[A-Z]\.?)?)*$",
            candidate,
        ))
        if has_chinese or has_sep or is_english_name:
            authors = candidate
            title = before_pub[dot_idx + 1:].strip()

    if not title:
        title = before_pub

    title = re.sub(r"\s+", " ", title).strip().rstrip(".")

    if not title or len(title) < 5:
        return None

    # Clean journal: strip leading dot
    journal = after_pub.split(",")[0].strip() if after_pub else ""
    journal = re.sub(r"^\.\s*", "", journal)

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": journal,
        "pub_type": pub_type,
        "doi": doi,
    }


def parse_references_to_titles(pdf_path: str) -> list[str]:
    """Convenience: extract just the title list from a reference PDF."""
    return [r["title"] for r in parse_references_from_pdf(pdf_path)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper Retriever — 文献自动检索与下载助手",
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # download
    p_dl = sub.add_parser("download", help="从标题列表检索并下载论文 PDF")
    p_dl.add_argument("input", help="Input file: TXT (one title per line), CSV, or Excel")
    p_dl.add_argument("-o", "--output", default="papers", help="Output directory (default: papers)")
    p_dl.add_argument("--retry", type=int, default=3, help="Max download retries per paper (default: 3)")
    p_dl.add_argument("--email", default="", help="Email for Unpaywall API (required for best OA coverage; free at unpaywall.org)")
    p_dl.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between papers (default: 1.0)")

    # check
    p_check = sub.add_parser("check", help="扫描文献列表，生成可获取/需手动提供的状态清单")
    p_check.add_argument("input", help="Input file: TXT (one title per line), CSV, or Excel")
    p_check.add_argument("-o", "--output", default="workspaces/default/outputs/check", help="Output directory (default: workspace)")
    p_check.add_argument("--email", default="", help="Email for Unpaywall API")
    p_check.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between papers (default: 1.0)")

    # parse
    p_parse = sub.add_parser("parse", help="从 PDF 参考文献页解析结构化文献列表")
    p_parse.add_argument("input", help="PDF file containing references")
    p_parse.add_argument("-o", "--output", default="", help="Output JSON file path (default: <input>_parsed.json)")
    p_parse.add_argument("--titles-only", action="store_true", help="Only output title list (.txt)")

    args = parser.parse_args()

    if args.command == "parse":
        if not os.path.exists(args.input):
            print(f"Error: input file not found: {args.input}")
            sys.exit(1)

        refs = parse_references_from_pdf(args.input)
        print(f"Parsed {len(refs)} references:")

        if args.titles_only:
            out_path = args.output or Path(args.input).with_suffix(".titles.txt")
            titles = [r["title"] for r in refs]
            with open(out_path, "w", encoding="utf-8") as f:
                for t in titles:
                    f.write(t + "\n")
            for i, r in enumerate(refs, 1):
                print(f"  [{i}] {r['title'][:100]}")
            print(f"\nTitles written to: {out_path}")
        else:
            out_path = args.output or Path(args.input).with_suffix(".parsed.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(refs, f, ensure_ascii=False, indent=2)
            for i, r in enumerate(refs, 1):
                print(f"  [{i}] {r['authors']} ({r['year']}) — {r['title'][:80]}")
            print(f"\nJSON written to: {out_path}")

    elif args.command == "check":
        if not os.path.exists(args.input):
            print(f"Error: input file not found: {args.input}")
            sys.exit(1)
        retriever = PaperRetriever(
            input_path=args.input,
            output_dir=args.output,
            email=args.email,
            delay=args.delay,
        )
        retriever.check()

    elif args.command == "download" or args.command is None:
        input_path = args.input if hasattr(args, "input") else sys.argv[1] if len(sys.argv) > 1 else None
        if not input_path:
            parser.print_help()
            sys.exit(1)
        if not os.path.exists(input_path):
            print(f"Error: input file not found: {input_path}")
            sys.exit(1)
        retriever = PaperRetriever(
            input_path=input_path,
            output_dir=args.output,
            max_retries=args.retry,
            email=args.email,
            delay=args.delay,
        )
        retriever.run()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
