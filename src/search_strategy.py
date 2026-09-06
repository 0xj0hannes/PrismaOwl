"""
LLM-assisted search-strategy (query) builder.

The strategy is a JSON document persisted at ``search_strategy.json`` in the
project root (next to ``criteria.json``) and shared by the CLI and the web UI:

{
  "research_question": "...",
  "scope_notes": "...",
  "concepts": [
    {"name": "Cybercrime", "terms": ["cybercrim*", "computer crime", ...]}
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
from typing import Any, Dict, List, Optional

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
