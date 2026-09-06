"""
LLM-assisted search-strategy (query) builder.

The strategy is a JSON document stored in the app's SQLite database (the
``documents`` table, key ``search_strategy``; see ``config.load_search_strategy``)
and exportable as a file:

{
  "research_question": "...",
  "scope_notes": "...",
  "concepts": [
    {"name": "Intervention", "terms": ["mindfulness*", "meditation", ...]}
  ],
  "queries": {
    "scopus": "TITLE-ABS-KEY(...)",
    "ieee": "...", "acm": "...", "web_of_science": "...",
    "openalex": "...", "semantic_scholar": "...", "arxiv": "...", "crossref": "..."
  },
  "rationale": "why the concepts / terms were chosen",
  "limitations": "known gaps of the query"
}

Every key under ``queries`` corresponds to a database. The ones supported by
``src/harvest.py`` can be run automatically; the others (ACM, Web of Science)
are meant to be pasted into the database's advanced-search box and the result
exported as BibTeX for ingestion.
"""
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .llm import generate_json

# Databases the strategy builder produces queries for, with syntax hints
# handed to the model. Keys are stable identifiers used across CLI/web/harvest.
DATABASES: Dict[str, Dict[str, str]] = {
    "scopus": {
        "label": "Scopus",
        "syntax": "Scopus advanced search: TITLE-ABS-KEY(...) with AND / OR / AND NOT, "
                  "double quotes for phrases, * for truncation, W/n proximity.",
    },
    "web_of_science": {
        "label": "Web of Science",
        "syntax": "Web of Science advanced search: TS=(...) for topic, with AND / OR / NOT, "
                  "quotes for phrases, * truncation, NEAR/n proximity.",
    },
    "ieee": {
        "label": "IEEE Xplore",
        "syntax": "IEEE Xplore command search: (\"Abstract\":term OR \"Document Title\":term) "
                  "with AND / OR / NOT, quotes for phrases, * wildcard (max 2 per term).",
    },
    "acm": {
        "label": "ACM Digital Library",
        "syntax": "ACM DL advanced search: Abstract:(...) OR Title:(...) OR Keyword:(...), "
                  "with AND / OR / NOT, quotes for phrases.",
    },
    "openalex": {
        "label": "OpenAlex",
        "syntax": "Plain boolean full-text search string: terms joined with AND / OR / NOT, "
                  "quotes for phrases, no field prefixes, no wildcards.",
    },
    "semantic_scholar": {
        "label": "Semantic Scholar",
        "syntax": "Semantic Scholar bulk search: + for AND, | for OR, - for NOT, quotes for phrases, "
                  "* suffix wildcard, parentheses for grouping.",
    },
    "arxiv": {
        "label": "arXiv",
        "syntax": "arXiv API: all:\"phrase\" or ti:/abs: field prefixes joined with AND / OR / ANDNOT, "
                  "parentheses for grouping.",
    },
    "crossref": {
        "label": "Crossref",
        "syntax": "Crossref does not support boolean operators; provide a compact list of the most "
                  "distinctive keywords separated by spaces.",
    },
}

# --------------------------------------------------------------------------
# Deterministic query builder (no LLM): concepts -> one query per database
# --------------------------------------------------------------------------
#
# Concepts are combined with AND, the terms inside a concept with OR. Each
# database gets the same semantic content in its own syntax, so editing a
# concept block and pressing "Rebuild" gives reproducible queries instantly.
# A year range parsed from the scope notes is embedded where the query
# language supports it (Scopus, Web of Science, arXiv) and, for the API
# harvesters, passed as a request parameter (see src/harvest.py).

_YEAR = r"((?:19|20)\d{2})"

# How each database applies a year range from the scope notes.
YEAR_FILTER_SUPPORT: Dict[str, str] = {
    "scopus": "query",            # PUBYEAR clause in the query string
    "web_of_science": "query",    # PY=(a-b) clause in the query string
    "arxiv": "query",             # submittedDate:[...] clause in the query string
    "openalex": "api",            # filter=from_publication_date / to_publication_date
    "semantic_scholar": "api",    # year=a-b
    "crossref": "api",            # filter=from-pub-date / until-pub-date
    "ieee": "api",                # start_year / end_year
    "acm": "manual",              # no date syntax: use the website's filters
}


def parse_year_range(scope_notes: str) -> Tuple[Optional[int], Optional[int]]:
    """Extract a publication-year range from free-text scope notes.

    Understands ``2010-2024`` / ``2010 to 2024`` / ``between 2010 and 2024``,
    ``2010 onwards`` / ``since 2010`` / ``from 2010``, ``after 2010`` (= 2011
    onwards), ``until 2020`` / ``up to 2020``, ``before 2020`` (= up to 2019)
    and ``last 10 years``. Returns ``(start, end)`` with ``None`` for an open
    side, or ``(None, None)`` when nothing is found.
    """
    text = " ".join((scope_notes or "").lower().split())
    if not text:
        return (None, None)
    m = re.search(rf"between\s+{_YEAR}\s+and\s+{_YEAR}", text) \
        or re.search(rf"{_YEAR}\s*(?:-|\u2013|\u2014|to|through|until|till)\s*{_YEAR}", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))
    m = re.search(rf"{_YEAR}\s*(?:onwards?|and later|or later|and after|\+)", text) \
        or re.search(rf"(?:since|from|starting(?: in| from)?)\s+{_YEAR}", text)
    if m:
        return (int(m.group(1)), None)
    m = re.search(rf"after\s+{_YEAR}", text)
    if m:
        return (int(m.group(1)) + 1, None)
    m = re.search(rf"(?:until|up to|through|till)\s+{_YEAR}", text)
    if m:
        return (None, int(m.group(1)))
    m = re.search(rf"(?:before|prior to)\s+{_YEAR}", text)
    if m:
        return (None, int(m.group(1)) - 1)
    m = re.search(r"(?:last|past)\s+(\d{1,2})\s+years?", text)
    if m:
        return (date.today().year - int(m.group(1)), None)
    return (None, None)


def format_year_range(years: Tuple[Optional[int], Optional[int]]) -> str:
    a, b = years
    if a is None and b is None:
        return ""
    if a is not None and b is not None:
        return f"{a}\u2013{b}"
    return f"{a} onwards" if a is not None else f"up to {b}"


def _clean_term(term: str, wildcard: bool = True) -> str:
    t = " ".join(str(term).split()).strip().strip('"').strip("'")
    if not wildcard:
        t = t.rstrip("*")
    return t


def _fmt(term: str, wildcard: bool = True) -> str:
    t = _clean_term(term, wildcard)
    if not t:
        return ""
    return f'"{t}"' if " " in t else t


def _term_lists(concepts: List[Dict[str, Any]]) -> List[List[str]]:
    out = []
    for c in concepts or []:
        terms = c.get("terms", []) if isinstance(c, dict) else []
        if isinstance(terms, str):
            terms = [t for t in terms.split(";")]
        cleaned = [_clean_term(t) for t in terms]
        cleaned = [t for t in cleaned if t]
        if cleaned:
            out.append(cleaned)
    return out


def _no_wildcard_terms(terms: List[str]) -> List[str]:
    """Terms for a database without truncation. A stem like ``cybercrim*`` is
    dropped when a sibling single-word term already starts with that stem
    (``cybercriminal`` covers it); a phrase sibling ("mindfulness-based stress
    reduction") does not count, because it would not match the plain word.
    Otherwise the bare stem is kept and the database's own stemming / partial
    matching has to do the work."""
    out = []
    for t in terms:
        if t.endswith("*"):
            stem = t.rstrip("*").lower()
            if any(o is not t and not o.endswith("*") and " " not in o and o.lower().startswith(stem)
                   for o in terms):
                continue
            t = t.rstrip("*")
        if t and t not in out:
            out.append(t)
    return out


def _or_group(terms: List[str], wildcard: bool = True, sep: str = " OR ") -> str:
    if not wildcard:
        terms = _no_wildcard_terms(terms)
    parts = [x for x in (_fmt(t, wildcard) for t in terms) if x]
    return "(" + sep.join(parts) + ")"


def build_queries(concepts: List[Dict[str, Any]], scope_notes: str = "") -> Dict[str, str]:
    """Compose one query per database from the concept blocks (no LLM)."""
    groups = _term_lists(concepts)
    if not groups:
        return {key: "" for key in DATABASES}
    start, end = parse_year_range(scope_notes)
    q: Dict[str, str] = {}

    # Scopus: TITLE-ABS-KEY(...) AND PUBYEAR > a-1 AND PUBYEAR < b+1
    core = " AND ".join(_or_group(g) for g in groups)
    scopus = f"TITLE-ABS-KEY({core})"
    if start is not None:
        scopus += f" AND PUBYEAR > {start - 1}"
    if end is not None:
        scopus += f" AND PUBYEAR < {end + 1}"
    q["scopus"] = scopus

    # Web of Science: TS=(...) AND PY=(a-b)
    wos = f"TS=({core})"
    if start is not None or end is not None:
        wos += f" AND PY=({start or 1900}-{end or date.today().year})"
    q["web_of_science"] = wos

    # IEEE Xplore command search: every term against Abstract and Document Title.
    ieee_groups = []
    for g in groups:
        parts = []
        for t in g:
            ft = _fmt(t)
            parts.append(f'"Abstract":{ft} OR "Document Title":{ft}')
        ieee_groups.append("(" + " OR ".join(parts) + ")")
    q["ieee"] = " AND ".join(ieee_groups)

    # ACM DL: (Abstract:(...) OR Title:(...) OR Keyword:(...)) per concept.
    acm_groups = []
    for g in groups:
        inner = " OR ".join(x for x in (_fmt(t) for t in g) if x)
        acm_groups.append(f"(Abstract:({inner}) OR Title:({inner}) OR Keyword:({inner}))")
    q["acm"] = " AND ".join(acm_groups)

    # OpenAlex: plain boolean, no wildcards (years go to the API filter).
    q["openalex"] = " AND ".join(_or_group(g, wildcard=False) for g in groups)

    # Semantic Scholar bulk search: + for AND, | for OR, * suffix wildcard.
    q["semantic_scholar"] = " + ".join(_or_group(g, sep=" | ") for g in groups)

    # arXiv: all: prefix per term, no wildcards, submittedDate clause for years.
    arxiv_groups = []
    for g in groups:
        parts = [f"all:{x}" for x in (_fmt(t, wildcard=False) for t in _no_wildcard_terms(g)) if x]
        arxiv_groups.append("(" + " OR ".join(parts) + ")")
    arxiv = " AND ".join(arxiv_groups)
    if start is not None or end is not None:
        arxiv += f" AND submittedDate:[{start or 1991}0101 TO {end or date.today().year}1231]"
    q["arxiv"] = arxiv

    # Crossref: no boolean operators, a compact keyword list (first terms per concept).
    words = []
    for g in groups:
        for t in _no_wildcard_terms(g)[:3]:
            words.append(_clean_term(t, wildcard=False).replace('"', ""))
    q["crossref"] = " ".join(words)

    for key in DATABASES:
        q.setdefault(key, "")
    return q


STRATEGY_PROMPT = """
You are an expert research librarian helping design a reproducible search strategy for a
systematic literature review (PRISMA 2020) or Systematization of Knowledge (SoK) paper.

**Research question / topic**
{topic}

{context_block}
**Task**
1. Decompose the topic into 2-4 orthogonal search concepts (population / phenomenon /
   context style blocks). For each concept list 5-15 synonyms, spelling variants,
   abbreviations and controlled-vocabulary terms. Use truncation where it helps.
2. Combine the concepts with AND, and the terms inside a concept with OR.
3. Produce one ready-to-paste query string per database below, obeying each database's
   syntax exactly. Keep the semantic content identical across databases.
4. Explain briefly why the concepts/terms were chosen and what the strategy may miss.

**Database syntax reference**
{db_syntax}

**Output Format (JSON)**
Respond ONLY with a valid JSON object matching this schema:
{schema}
"""


def _schema() -> str:
    schema = {
        "research_question": "string - restated precisely",
        "scope_notes": "string - date range, languages, document types, etc.",
        "concepts": [{"name": "string", "terms": ["string", "..."]}],
        "queries": {key: "string - query for this database" for key in DATABASES},
        "rationale": "string",
        "limitations": "string",
    }
    return json.dumps(schema, indent=2)


def build_strategy_prompt(topic: str, current: Optional[Dict[str, Any]] = None,
                          feedback: str = "") -> str:
    context_parts = []
    if current:
        context_parts.append("**Current strategy (refine it, keep what works)**\n"
                             + json.dumps(current, indent=2, ensure_ascii=False))
    if feedback:
        context_parts.append(f"**Researcher feedback to incorporate**\n{feedback}")
    context_block = ("\n\n".join(context_parts) + "\n") if context_parts else ""

    db_syntax = "\n".join(f"- {key} ({d['label']}): {d['syntax']}" for key, d in DATABASES.items())
    return STRATEGY_PROMPT.format(topic=topic.strip(), context_block=context_block,
                                  db_syntax=db_syntax, schema=_schema())


def normalize_strategy(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce whatever the model (or the user) sent into the canonical shape."""
    concepts: List[Dict[str, Any]] = []
    for c in data.get("concepts", []) or []:
        if isinstance(c, dict):
            terms = c.get("terms", [])
            if isinstance(terms, str):
                terms = [t.strip() for t in terms.split(";") if t.strip()]
            concepts.append({"name": str(c.get("name", "")).strip(),
                             "terms": [str(t).strip() for t in terms if str(t).strip()]})
    queries_in = data.get("queries", {}) or {}
    queries = {key: str(queries_in.get(key, "") or "").strip() for key in DATABASES}
    # Preserve any extra databases the user added by hand.
    for key, val in queries_in.items():
        if key not in queries:
            queries[str(key)] = str(val or "").strip()
    return {
        "research_question": str(data.get("research_question", "") or "").strip(),
        "scope_notes": str(data.get("scope_notes", "") or "").strip(),
        "concepts": concepts,
        "queries": queries,
        "rationale": str(data.get("rationale", "") or "").strip(),
        "limitations": str(data.get("limitations", "") or "").strip(),
    }


def generate_strategy(topic: str, current: Optional[Dict[str, Any]] = None,
                      feedback: str = "") -> Dict[str, Any]:
    """Ask the LLM for a (new or refined) search strategy."""
    if not topic.strip() and current:
        topic = current.get("research_question", "")
    if not topic.strip():
        raise ValueError("A research question / topic is required.")
    prompt = build_strategy_prompt(topic, current=current, feedback=feedback)
    return normalize_strategy(generate_json(prompt, task="query"))


def format_strategy(strategy: Dict[str, Any]) -> str:
    """Human-readable rendering for the CLI."""
    lines = [f"Research question: {strategy.get('research_question', '')}"]
    if strategy.get("scope_notes"):
        lines.append(f"Scope: {strategy['scope_notes']}")
    lines.append("")
    lines.append("Concepts:")
    for c in strategy.get("concepts", []):
        lines.append(f"  - {c.get('name')}: {', '.join(c.get('terms', []))}")
    lines.append("")
    lines.append("Queries:")
    for key, q in strategy.get("queries", {}).items():
        label = DATABASES.get(key, {}).get("label", key)
        lines.append(f"  [{label}]")
        lines.append(f"    {q}")
    if strategy.get("rationale"):
        lines.append("")
        lines.append(f"Rationale: {strategy['rationale']}")
    if strategy.get("limitations"):
        lines.append(f"Limitations: {strategy['limitations']}")
    return "\n".join(lines)
