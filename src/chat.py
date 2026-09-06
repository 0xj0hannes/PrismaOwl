"""
Chatbot over the screened corpus.

The bot answers questions about the records that made it through screening.
All matching records (title, authors, year, DOI, abstract, per-criterion
scores/rationales) are placed in the system prompt, so the model can cite them
by ID. Frontier models routed via OrcaRouter have 200k-1M token windows, enough
for several hundred abstracts; above ``MAX_CONTEXT_CHARS`` abstracts are
truncated so the request still fits (set MODEL_CHAT to a long-context model).

Used by both ``main.py chat`` (JSON files) and ``/api/chat`` (SQLite).
"""
from typing import Any, Dict, Iterable, List, Tuple, Optional

from .llm import generate_chat

SCOPES = {
    "included": ("Include",),
    "included_maybe": ("Include", "Maybe"),
    "all_screened": ("Include", "Maybe", "Exclude"),
}

MAX_CONTEXT_CHARS = 600_000  # ~150k tokens; fits 200k+ context models
ABSTRACT_CAP_WHEN_TRUNCATING = 600

SYSTEM_TEMPLATE = """You are a research assistant embedded in PrismaOwl, a PRISMA/SoK screening tool.
You answer questions about the corpus of papers listed below, which are the records that passed
title/abstract screening for the review (scope: {scope_label}; {n} records).

Rules:
- Ground every claim in the corpus. Cite records by their ID in square brackets, e.g. [{example_id}].
- If the corpus does not contain the answer, say so plainly instead of guessing.
- When asked for overviews, group papers by theme and mention counts.
- Keep answers concise; use bullet lists for enumerations.
{criteria_block}{focus_block}
=== CORPUS ===
{corpus}
=== END CORPUS ===
"""

FOCUS_TEMPLATE = """
=== RECORD UNDER REVIEW ===
The human reviewer is currently deciding whether to include this record. When asked about it,
assess it against each inclusion criterion in turn, quoting the title/abstract as evidence, say
clearly what the abstract leaves uncertain, and end with a recommendation (Include, Exclude, or
"needs the full text") - the reviewer makes the final decision, not you.
{entry}
=== END RECORD UNDER REVIEW ===
"""


def effective_decision(result: Dict[str, Any]) -> str:
    return result.get("final_decision") or result.get("decision", "")


def select_records(records: Iterable[Dict[str, Any]], results: Dict[str, Dict[str, Any]],
                   scope: str = "included") -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (record, result) pairs whose effective decision is in ``scope``."""
    wanted = SCOPES.get(scope)
    if wanted is None:
        raise ValueError(f"Unknown scope '{scope}'. Choose from {', '.join(SCOPES)}.")
    selected = []
    for rec in records:
        if rec.get("is_duplicate"):
            continue
        res = results.get(rec["id"])
        if not res:
            continue
        if effective_decision(res) in wanted:
            selected.append((rec, res))
    return selected


def _format_entry(rec: Dict[str, Any], res: Dict[str, Any], abstract_cap: int = 0) -> str:
    abstract = (rec.get("abstract") or "").strip()
    if abstract_cap and len(abstract) > abstract_cap:
        abstract = abstract[:abstract_cap].rstrip() + "…"
    lines = [f"[{rec['id']}] {rec.get('title', '').strip()}"]
    meta = []
    if rec.get("authors"):
        meta.append(rec["authors"])
    if rec.get("year"):
        meta.append(str(rec["year"]))
    if rec.get("doi"):
        meta.append(f"doi:{rec['doi']}")
    if meta:
        lines.append("  " + " | ".join(meta))
    lines.append(f"  Decision: {effective_decision(res)}"
                 + (" (human reviewed)" if res.get("human_reviewed") else ""))
    for key, c in (res.get("criteria") or {}).items():
        if not isinstance(c, dict):
            continue
        score = c.get("score")
        rationale = (c.get("rationale") or "").strip()
        lines.append(f"  {key} score={score}: {rationale}")
    if abstract:
        lines.append(f"  Abstract: {abstract}")
    return "\n".join(lines)


def build_corpus(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]]) -> str:
    corpus = "\n\n".join(_format_entry(r, s) for r, s in pairs)
    if len(corpus) > MAX_CONTEXT_CHARS:
        corpus = "\n\n".join(_format_entry(r, s, ABSTRACT_CAP_WHEN_TRUNCATING) for r, s in pairs)
    if len(corpus) > MAX_CONTEXT_CHARS:
        corpus = corpus[:MAX_CONTEXT_CHARS] + "\n\n[corpus truncated]"
    return corpus


def build_system_prompt(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]], scope: str,
                        criteria: Dict[str, Any],
                        focus: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None) -> str:
    scope_label = {
        "included": "included records only",
        "included_maybe": "included records plus unresolved Maybe cases",
        "all_screened": "every screened record, including excluded ones",
    }.get(scope, scope)
    criteria_block = ""
    if criteria:
        lines = ["", "Inclusion criteria used during screening:"]
        for key, c in criteria.items():
            lines.append(f"- {key} ({c.get('name', '')}): {c.get('definition', '')}")
        criteria_block = "\n".join(lines) + "\n"
    example_id = pairs[0][0]["id"] if pairs else "record_id"
    focus_block = ""
    if focus is not None:
        focus_block = FOCUS_TEMPLATE.format(entry=_format_entry(focus[0], focus[1] or {}))
        example_id = focus[0]["id"]
    return SYSTEM_TEMPLATE.format(scope_label=scope_label, n=len(pairs), example_id=example_id,
                                  criteria_block=criteria_block, focus_block=focus_block,
                                  corpus=build_corpus(pairs) if pairs else "(no records)")


def ask(messages: List[Dict[str, str]], records: Iterable[Dict[str, Any]],
        results: Dict[str, Dict[str, Any]], criteria: Dict[str, Any],
        scope: str = "included", focus_record_id: Optional[str] = None) -> Dict[str, Any]:
    """Answer the latest user message given the full chat history.

    ``focus_record_id`` names the record the reviewer is currently deciding
    on: it is placed in a dedicated block of the system prompt (with its full
    abstract, scores and rationales) whatever the scope, so the assistant can
    weigh it against the criteria."""
    records = list(records)
    pairs = select_records(records, results, scope)
    focus = None
    if focus_record_id:
        rec = next((r for r in records if r.get("id") == focus_record_id), None)
        if rec is None:
            raise ValueError(f"Record '{focus_record_id}' not found.")
        focus = (rec, results.get(focus_record_id) or {})
    system_prompt = build_system_prompt(pairs, scope, criteria, focus=focus)
    reply = generate_chat(messages, system_prompt)
    out = {"reply": reply, "n_records": len(pairs), "scope": scope}
    if focus is not None:
        out["focus_id"] = focus_record_id
    return out
