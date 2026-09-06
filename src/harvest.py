"""
Automated literature harvesting: run a search query against a bibliographic
database's official API and write the hits as a BibTeX file that
``src/ingestion.load_bibtex`` can read.

Supported sources (identifiers match ``search_strategy.DATABASES``):

    openalex          no key needed (set OPENALEX_EMAIL for the polite pool)
    semantic_scholar  key optional (SEMANTIC_SCHOLAR_API_KEY raises rate limits)
    arxiv             no key needed
    crossref          no key needed (abstracts are often missing)
    scopus            SCOPUS_API_KEY required (+ SCOPUS_INST_TOKEN for abstracts)
    ieee              IEEE_API_KEY required

ACM Digital Library and Web of Science have no public search API: paste the
generated query into their advanced search and export BibTeX manually.

Only official APIs are used and every request is throttled; no HTML scraping.
"""
import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import requests
import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter

from .config import load_config

HARVEST_DIR = "data/harvest"
USER_AGENT = "PrismaOwl/1.0 (https://github.com/0xj0hannes/PrismaOwl)"
DEFAULT_TIMEOUT = 60


class HarvestError(RuntimeError):
    pass


@dataclass
class Paper:
    title: str
    abstract: str = ""
    authors: List[str] = field(default_factory=list)
    year: str = ""
    doi: str = ""
    venue: str = ""
    url: str = ""
    entry_type: str = "misc"  # article | inproceedings | misc
    volume: str = ""
    number: str = ""
    pages: str = ""
    keywords: List[str] = field(default_factory=list)
    source_db: str = ""
    source_id: str = ""


ProgressFn = Optional[Callable[[int, Optional[int]], None]]
YearRange = Tuple[Optional[int], Optional[int]]   # (from_year, to_year), either side optional


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def _get(url: str, params: Dict[str, Any] = None, headers: Dict[str, str] = None,
         retries: int = 3) -> requests.Response:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=hdrs, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last = HarvestError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
            retry_after = resp.headers.get("Retry-After")
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            raise HarvestError(f"HTTP {resp.status_code} from {url}: {resp.text[:300]}")
        return resp
    raise HarvestError(str(last) if last else f"Request to {url} failed")


def _clean_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    return doi


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# --------------------------------------------------------------------------
# Providers. Each yields Paper objects until exhausted or max_results reached.
# --------------------------------------------------------------------------

def _openalex(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
              years: YearRange = (None, None)) -> Iterator[Paper]:
    params = {"search": query, "per-page": min(200, max_results), "cursor": "*"}
    if cfg.get("OPENALEX_EMAIL"):
        params["mailto"] = cfg["OPENALEX_EMAIL"]
    filters = []
    if years[0] is not None:
        filters.append(f"from_publication_date:{years[0]}-01-01")
    if years[1] is not None:
        filters.append(f"to_publication_date:{years[1]}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    fetched = 0
    while fetched < max_results:
        data = _get("https://api.openalex.org/works", params).json()
        total = data.get("meta", {}).get("count")
        for w in data.get("results", []):
            inv = w.get("abstract_inverted_index") or {}
            abstract = ""
            if inv:
                positions = {}
                for word, idxs in inv.items():
                    for i in idxs:
                        positions[i] = word
                abstract = " ".join(positions[i] for i in sorted(positions))
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            wtype = w.get("type", "")
            biblio = w.get("biblio") or {}
            pages = ""
            if biblio.get("first_page"):
                pages = biblio["first_page"] + (f"--{biblio['last_page']}" if biblio.get("last_page") else "")
            yield Paper(
                title=w.get("title") or w.get("display_name") or "",
                abstract=abstract,
                authors=[a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])],
                year=str(w.get("publication_year") or ""),
                doi=_clean_doi(w.get("doi") or ""),
                venue=src.get("display_name") or "",
                url=w.get("id") or "",
                entry_type="article" if wtype == "article" and src.get("type") == "journal"
                else "inproceedings" if src.get("type") == "conference" or wtype == "proceedings-article"
                else "misc",
                volume=str(biblio.get("volume") or ""),
                number=str(biblio.get("issue") or ""),
                pages=pages,
                keywords=[k.get("display_name", "") for k in w.get("keywords", []) if k.get("display_name")],
                source_db="openalex",
                source_id=w.get("id", ""),
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not data.get("results"):
            break
        params["cursor"] = cursor
        time.sleep(0.2)


def _semantic_scholar(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
                      years: YearRange = (None, None)) -> Iterator[Paper]:
    headers = {}
    if cfg.get("SEMANTIC_SCHOLAR_API_KEY"):
        headers["x-api-key"] = cfg["SEMANTIC_SCHOLAR_API_KEY"]
    params = {"query": query,
              "fields": "title,abstract,year,authors,externalIds,venue,publicationTypes,journal,url"}
    if years[0] is not None or years[1] is not None:
        params["year"] = f"{years[0] or ''}-{years[1] or ''}"
    fetched = 0
    while fetched < max_results:
        data = _get("https://api.semanticscholar.org/graph/v1/paper/search/bulk", params, headers).json()
        total = data.get("total")
        for p in data.get("data", []):
            ext = p.get("externalIds") or {}
            journal = p.get("journal") or {}
            ptypes = p.get("publicationTypes") or []
            yield Paper(
                title=p.get("title") or "",
                abstract=p.get("abstract") or "",
                authors=[a.get("name", "") for a in p.get("authors", [])],
                year=str(p.get("year") or ""),
                doi=_clean_doi(ext.get("DOI") or ""),
                venue=p.get("venue") or journal.get("name") or "",
                url=p.get("url") or "",
                entry_type="inproceedings" if "Conference" in ptypes
                else "article" if "JournalArticle" in ptypes else "misc",
                volume=str(journal.get("volume") or ""),
                pages=str(journal.get("pages") or ""),
                source_db="semantic_scholar",
                source_id=p.get("paperId", ""),
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        token = data.get("token")
        if not token or not data.get("data"):
            break
        params["token"] = token
        time.sleep(1.0 if not headers else 0.2)


_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom",
             "os": "http://a9.com/-/spec/opensearch/1.1/"}


def _arxiv(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
           years: YearRange = (None, None)) -> Iterator[Paper]:
    if (years[0] is not None or years[1] is not None) and "submitteddate" not in query.lower():
        query = (f"({query}) AND submittedDate:[{years[0] or 1991}0101 TO "
                 f"{years[1] or datetime.now().year}1231]")
    start, page = 0, 100
    fetched = 0
    while fetched < max_results:
        params = {"search_query": query, "start": start, "max_results": min(page, max_results - fetched)}
        root = ET.fromstring(_get("https://export.arxiv.org/api/query", params).text)
        total_el = root.find("os:totalResults", _ARXIV_NS)
        total = int(total_el.text) if total_el is not None and total_el.text else None
        entries = root.findall("atom:entry", _ARXIV_NS)
        if not entries:
            break
        for e in entries:
            def txt(tag, ns="atom"):
                el = e.find(f"{ns}:{tag}", _ARXIV_NS)
                return (el.text or "").strip() if el is not None else ""
            arxiv_id = txt("id")
            published = txt("published")
            yield Paper(
                title=re.sub(r"\s+", " ", txt("title")),
                abstract=re.sub(r"\s+", " ", txt("summary")),
                authors=[(a.find("atom:name", _ARXIV_NS).text or "").strip()
                         for a in e.findall("atom:author", _ARXIV_NS)],
                year=published[:4],
                doi=_clean_doi(txt("doi", "arxiv")),
                venue="arXiv",
                url=arxiv_id,
                entry_type="misc",
                keywords=[c.get("term", "") for c in e.findall("atom:category", _ARXIV_NS)],
                source_db="arxiv",
                source_id=arxiv_id.rsplit("/", 1)[-1],
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        start += len(entries)
        if total is not None and start >= total:
            break
        time.sleep(3.0)  # arXiv API terms of use


def _crossref(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
              years: YearRange = (None, None)) -> Iterator[Paper]:
    params = {"query.bibliographic": query, "rows": min(100, max_results), "cursor": "*",
              "select": "DOI,title,abstract,author,issued,container-title,type,volume,issue,page,URL,subject"}
    if cfg.get("OPENALEX_EMAIL"):
        params["mailto"] = cfg["OPENALEX_EMAIL"]
    filters = []
    if years[0] is not None:
        filters.append(f"from-pub-date:{years[0]}")
    if years[1] is not None:
        filters.append(f"until-pub-date:{years[1]}")
    if filters:
        params["filter"] = ",".join(filters)
    fetched = 0
    while fetched < max_results:
        msg = _get("https://api.crossref.org/works", params).json().get("message", {})
        total = msg.get("total-results")
        items = msg.get("items", [])
        for it in items:
            date_parts = (it.get("issued") or {}).get("date-parts") or [[None]]
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            ctype = it.get("type", "")
            yield Paper(
                title=" ".join(it.get("title") or []),
                abstract=_strip_tags(it.get("abstract") or ""),
                authors=[" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
                         for a in it.get("author", [])],
                year=str(year or ""),
                doi=_clean_doi(it.get("DOI") or ""),
                venue=" ".join(it.get("container-title") or []),
                url=it.get("URL") or "",
                entry_type="article" if ctype == "journal-article"
                else "inproceedings" if ctype == "proceedings-article" else "misc",
                volume=str(it.get("volume") or ""),
                number=str(it.get("issue") or ""),
                pages=str(it.get("page") or "").replace("-", "--"),
                keywords=list(it.get("subject") or []),
                source_db="crossref",
                source_id=it.get("DOI", ""),
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        cursor = msg.get("next-cursor")
        if not cursor or not items:
            break
        params["cursor"] = cursor
        time.sleep(0.5)


def _scopus(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
            years: YearRange = (None, None)) -> Iterator[Paper]:
    key = cfg.get("SCOPUS_API_KEY")
    if not key:
        raise HarvestError("SCOPUS_API_KEY is not set in .env")
    if "pubyear" not in query.lower():
        if years[0] is not None:
            query += f" AND PUBYEAR > {years[0] - 1}"
        if years[1] is not None:
            query += f" AND PUBYEAR < {years[1] + 1}"
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    if cfg.get("SCOPUS_INST_TOKEN"):
        headers["X-ELS-Insttoken"] = cfg["SCOPUS_INST_TOKEN"]
    view = "COMPLETE"
    start, fetched = 0, 0
    while fetched < max_results:
        params = {"query": query, "count": 25, "start": start, "view": view}
        try:
            data = _get("https://api.elsevier.com/content/search/scopus", params, headers).json()
        except HarvestError as e:
            # COMPLETE view (abstracts) needs an entitled key; degrade gracefully.
            if view == "COMPLETE" and ("401" in str(e) or "403" in str(e)):
                view = "STANDARD"
                continue
            raise
        sr = data.get("search-results", {})
        total = int(sr.get("opensearch:totalResults", 0) or 0)
        entries = [e for e in sr.get("entry", []) if "error" not in e]
        if not entries:
            break
        for e in entries:
            authors = [a.get("authname", "") for a in e.get("author", [])] or \
                      ([e["dc:creator"]] if e.get("dc:creator") else [])
            agg = (e.get("prism:aggregationType") or "").lower()
            yield Paper(
                title=e.get("dc:title") or "",
                abstract=e.get("dc:description") or "",
                authors=authors,
                year=(e.get("prism:coverDate") or "")[:4],
                doi=_clean_doi(e.get("prism:doi") or ""),
                venue=e.get("prism:publicationName") or "",
                url=next((l.get("@href") for l in e.get("link", []) if l.get("@ref") == "scopus"), ""),
                entry_type="inproceedings" if "conference" in agg else "article" if "journal" in agg else "misc",
                volume=str(e.get("prism:volume") or ""),
                number=str(e.get("prism:issueIdentifier") or ""),
                pages=str(e.get("prism:pageRange") or "").replace("-", "--"),
                keywords=[k.strip() for k in (e.get("authkeywords") or "").split("|") if k.strip()],
                source_db="scopus",
                source_id=e.get("eid", ""),
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        start += len(entries)
        if start >= total:
            break
        time.sleep(0.3)


def _ieee(query: str, max_results: int, cfg: Dict[str, Any], progress: ProgressFn,
          years: YearRange = (None, None)) -> Iterator[Paper]:
    key = cfg.get("IEEE_API_KEY")
    if not key:
        raise HarvestError("IEEE_API_KEY is not set in .env")
    start, fetched = 1, 0
    while fetched < max_results:
        params = {"apikey": key, "querytext": query, "format": "json",
                  "max_records": min(200, max_results - fetched), "start_record": start}
        if years[0] is not None:
            params["start_year"] = years[0]
        if years[1] is not None:
            params["end_year"] = years[1]
        data = _get("https://ieeexploreapi.ieee.org/api/v1/search/articles", params).json()
        total = data.get("total_records")
        articles = data.get("articles", [])
        if not articles:
            break
        for a in articles:
            ctype = (a.get("content_type") or "").lower()
            kw = (a.get("index_terms") or {}).get("author_terms", {}).get("terms", []) or []
            pages = ""
            if a.get("start_page"):
                pages = str(a["start_page"]) + (f"--{a['end_page']}" if a.get("end_page") else "")
            yield Paper(
                title=a.get("title") or "",
                abstract=a.get("abstract") or "",
                authors=[x.get("full_name", "") for x in (a.get("authors") or {}).get("authors", [])],
                year=str(a.get("publication_year") or ""),
                doi=_clean_doi(a.get("doi") or ""),
                venue=a.get("publication_title") or "",
                url=a.get("html_url") or "",
                entry_type="inproceedings" if "conference" in ctype else "article" if "journal" in ctype else "misc",
                volume=str(a.get("volume") or ""),
                number=str(a.get("issue") or ""),
                pages=pages,
                keywords=list(kw),
                source_db="ieee",
                source_id=str(a.get("article_number") or ""),
            )
            fetched += 1
            if fetched >= max_results:
                break
        if progress:
            progress(fetched, total)
        start += len(articles)
        if total is not None and start > total:
            break
        time.sleep(0.5)


PROVIDERS: Dict[str, Callable[..., Iterator[Paper]]] = {
    "openalex": _openalex,
    "semantic_scholar": _semantic_scholar,
    "arxiv": _arxiv,
    "crossref": _crossref,
    "scopus": _scopus,
    "ieee": _ieee,
}

MANUAL_ONLY = {
    "acm": "ACM Digital Library has no public search API. Paste the query into "
           "https://dl.acm.org/search/advanced and export the results as BibTeX.",
    "web_of_science": "Web of Science requires an institutional API subscription. Paste the query into "
                      "the advanced search and export as 'BibTeX' (Full Record).",
}


def list_sources() -> List[Dict[str, Any]]:
    """Describe every harvestable source and whether it is usable right now."""
    cfg = load_config()
    info = [
        ("openalex", "OpenAlex", True, "No key needed. Set OPENALEX_EMAIL in .env for faster polite-pool access."),
        ("semantic_scholar", "Semantic Scholar", True,
         "Works without a key (slow, shared rate limit); SEMANTIC_SCHOLAR_API_KEY lifts limits."),
        ("arxiv", "arXiv", True, "No key needed. 3 s between pages per arXiv policy."),
        ("crossref", "Crossref", True, "No key needed. Abstracts are frequently missing; no boolean operators."),
        ("scopus", "Scopus", bool(cfg.get("SCOPUS_API_KEY")),
         "Needs SCOPUS_API_KEY (dev.elsevier.com); abstracts additionally need SCOPUS_INST_TOKEN / campus IP."),
        ("ieee", "IEEE Xplore", bool(cfg.get("IEEE_API_KEY")),
         "Needs IEEE_API_KEY (developer.ieee.org)."),
    ]
    out = [{"key": k, "label": lbl, "available": avail, "note": note, "manual": False}
           for k, lbl, avail, note in info]
    for k, note in MANUAL_ONLY.items():
        out.append({"key": k, "label": "ACM Digital Library" if k == "acm" else "Web of Science",
                    "available": False, "note": note, "manual": True})
    return out


# --------------------------------------------------------------------------
# BibTeX serialisation
# --------------------------------------------------------------------------

_BIB_ESCAPES = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def _bib_escape(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    text = "".join(_BIB_ESCAPES.get(ch, ch) for ch in text)
    # Unbalanced braces break parsers: drop braces entirely (titles keep case
    # because we wrap them in an outer brace pair at write time anyway).
    return text.replace("{", "").replace("}", "")


def _bib_person(name: str) -> str:
    """Normalise a display name to 'Last, First' for BibTeX."""
    name = name.strip()
    if not name or "," in name:
        return name
    parts = name.split()
    if len(parts) == 1:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def make_bib_key(paper: Paper, used: set) -> str:
    first = paper.authors[0] if paper.authors else "anon"
    last = _bib_person(first).split(",")[0]
    last = re.sub(r"[^A-Za-z]", "", last).lower() or "anon"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", paper.title)
                 if w.lower() not in {"with", "from", "that", "this", "using", "towards", "toward"}), "")
    base = f"{last}{paper.year or 'nd'}{word.lower()}"
    key, suffix = base, ""
    i = 0
    while key in used:
        i += 1
        suffix = chr(ord("a") + (i - 1) % 26) * (1 + (i - 1) // 26)
        key = base + suffix
    used.add(key)
    return key


def papers_to_bibtex(papers: List[Paper], query: str = "", source: str = "") -> str:
    db = BibDatabase()
    used: set = set()
    for p in papers:
        if not p.title:
            continue
        entry: Dict[str, str] = {
            "ENTRYTYPE": p.entry_type or "misc",
            "ID": make_bib_key(p, used),
            "title": _bib_escape(p.title),
        }
        if p.authors:
            entry["author"] = " and ".join(_bib_escape(_bib_person(a)) for a in p.authors if a.strip())
        if p.year:
            entry["year"] = p.year
        if p.abstract:
            entry["abstract"] = _bib_escape(p.abstract)
        if p.doi:
            entry["doi"] = p.doi
        if p.venue:
            entry["journal" if entry["ENTRYTYPE"] == "article" else "booktitle"] = _bib_escape(p.venue)
        if p.volume:
            entry["volume"] = p.volume
        if p.number:
            entry["number"] = p.number
        if p.pages:
            entry["pages"] = p.pages
        if p.url:
            entry["url"] = p.url
        if p.keywords:
            entry["keywords"] = _bib_escape("; ".join(k for k in p.keywords if k))
        if p.source_db:
            entry["source"] = p.source_db
        if p.source_id:
            entry["note"] = _bib_escape(f"{p.source_db} id: {p.source_id}")
        db.entries.append(entry)

    writer = BibTexWriter()
    writer.indent = "  "
    writer.order_entries_by = None
    header = ""
    if source or query:
        header = (f"% Generated by PrismaOwl harvest on {datetime.now():%Y-%m-%d %H:%M}\n"
                  f"% Source: {source}\n% Query: {query.replace(chr(10), ' ')}\n% Records: {len(db.entries)}\n\n")
    return header + bibtexparser.dumps(db, writer)


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------

def harvest(source: str, query: str, max_results: int = 500,
            progress: ProgressFn = None, years: YearRange = (None, None)) -> List[Paper]:
    """Run ``query`` against ``source`` and return up to ``max_results`` papers.

    ``years`` is an optional ``(from, to)`` publication-year range (either side
    may be ``None``); each provider applies it the way its API allows."""
    if source in MANUAL_ONLY:
        raise HarvestError(MANUAL_ONLY[source])
    provider = PROVIDERS.get(source)
    if provider is None:
        raise HarvestError(f"Unknown source '{source}'. Choose from: {', '.join(PROVIDERS)}")
    if not query.strip():
        raise HarvestError("Query is empty.")
    max_results = max(1, min(int(max_results), 5000))
    cfg = load_config()
    years = _normalize_years(years)
    return list(provider(query.strip(), max_results, cfg, progress, years))


def _normalize_years(years) -> YearRange:
    a, b = (tuple(years) + (None, None))[:2] if years else (None, None)
    a = int(a) if a not in (None, "") else None
    b = int(b) if b not in (None, "") else None
    if a is not None and b is not None and a > b:
        a, b = b, a
    return (a, b)


def harvest_to_file(source: str, query: str, output: Optional[str] = None,
                    max_results: int = 500, progress: ProgressFn = None,
                    years: YearRange = (None, None)) -> Dict[str, Any]:
    """Harvest and write a .bib file. Returns a summary dict."""
    years = _normalize_years(years)
    papers = harvest(source, query, max_results, progress, years)
    if output is None:
        os.makedirs(HARVEST_DIR, exist_ok=True)
        output = os.path.join(HARVEST_DIR, f"{source}_{datetime.now():%Y%m%d_%H%M%S}.bib")
    else:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(papers_to_bibtex(papers, query=query, source=source))
    n_abstracts = sum(1 for p in papers if p.abstract)
    return {"source": source, "query": query, "file": output, "count": len(papers),
            "with_abstract": n_abstracts, "years": list(years)}


def paper_dicts(papers: List[Paper]) -> List[Dict[str, Any]]:
    return [asdict(p) for p in papers]
