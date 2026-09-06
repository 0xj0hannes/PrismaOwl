"""
LLM-assisted inclusion-criteria drafting.

Produces (or refines) a dictionary in exactly the criteria shape used
by the rest of the pipeline::

    {"IC1": {"name", "definition", "signals", "negative_indicators"}, ...}

Nothing here hardcodes criterion keys; the model proposes them and the user
can rename/edit them in the web editor or by hand.
"""
import json
import re
from typing import Any, Dict, Optional

from .llm import generate_json

CRITERIA_FIELDS = ("name", "definition", "signals", "negative_indicators")

CRITERIA_PROMPT = """
You are a methodologist helping a research team define the inclusion criteria for a
systematic literature review / SoK. The criteria will later be given to an LLM screening
assistant that judges each paper's title and abstract against EVERY criterion separately,
so each criterion must be precise, independent and verifiable from an abstract alone.

**Research question / topic**
{topic}

{context_block}
**Task**
Propose {n_hint} inclusion criteria. For each criterion provide:
- "name": a 2-5 word label.
- "definition": one or two sentences stating exactly what the study must do/contain.
- "signals": comma-separated cues, keywords, constructs or study designs that indicate the
  criterion is met.
- "negative_indicators": comma-separated cues that look related but should NOT count
  (to keep precision high).

Use short stable keys such as "IC1", "IC2", ... (or keep the existing keys when refining).
Do not include exclusion-only criteria; express them as negative indicators instead.

**Output Format (JSON)**
Respond ONLY with a valid JSON object of the form:
{schema}
"""


def _schema() -> str:
    return json.dumps({
        "IC1": {f: "string" for f in CRITERIA_FIELDS},
        "IC2": {f: "string" for f in CRITERIA_FIELDS},
    }, indent=2)


def build_criteria_prompt(topic: str, current: Optional[Dict[str, Any]] = None,
                          feedback: str = "", count: Optional[int] = None) -> str:
    parts = []
    if current:
        parts.append("**Current criteria (refine these; keep keys unless asked otherwise)**\n"
                     + json.dumps(current, indent=2, ensure_ascii=False))
    if feedback:
        parts.append(f"**Researcher feedback to incorporate**\n{feedback}")
    context_block = ("\n\n".join(parts) + "\n") if parts else ""
    n_hint = str(count) if count else "2 to 5"
    return CRITERIA_PROMPT.format(topic=topic.strip(), context_block=context_block,
                                  n_hint=n_hint, schema=_schema())


_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def validate_criteria(data: Any) -> Dict[str, Dict[str, str]]:
    """Validate/normalize a criteria mapping. Raises ``ValueError`` on bad input.

    Keys must be identifier-like (they become CSV column prefixes and JSON keys
    in the screening prompt). Every field is coerced to a string; missing
    fields become empty strings so the prompt template never crashes.
    """
    if not isinstance(data, dict) or not data:
        raise ValueError("Criteria must be a non-empty JSON object keyed by criterion ID.")
    out: Dict[str, Dict[str, str]] = {}
    for key, val in data.items():
        key = str(key).strip()
        if not _KEY_RE.match(key):
            raise ValueError(f"Invalid criterion key '{key}': use letters, digits and underscores only.")
        if not isinstance(val, dict):
            raise ValueError(f"Criterion '{key}' must be an object with {', '.join(CRITERIA_FIELDS)}.")
        entry = {f: str(val.get(f, "") or "").strip() for f in CRITERIA_FIELDS}
        if not entry["name"] or not entry["definition"]:
            raise ValueError(f"Criterion '{key}' needs at least a name and a definition.")
        out[key] = entry
    return out


def generate_criteria(topic: str, current: Optional[Dict[str, Any]] = None,
                      feedback: str = "", count: Optional[int] = None) -> Dict[str, Dict[str, str]]:
    if not topic.strip():
        raise ValueError("A research question / topic is required.")
    prompt = build_criteria_prompt(topic, current=current, feedback=feedback, count=count)
    return validate_criteria(generate_json(prompt, task="criteria"))


def format_criteria(criteria: Dict[str, Dict[str, str]]) -> str:
    lines = []
    for key, c in criteria.items():
        lines.append(f"{key}: {c.get('name')}")
        lines.append(f"  Definition: {c.get('definition')}")
        lines.append(f"  Signals: {c.get('signals')}")
        lines.append(f"  Negative indicators: {c.get('negative_indicators')}")
        lines.append("")
    return "\n".join(lines).rstrip()
