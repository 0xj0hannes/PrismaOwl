"""
Centralized prompt templates for LLM screening.
"""
from typing import Dict, Any
import json

# How strictly the model decides between Include / Exclude / Maybe. The level
# only changes the decision rules below; criteria, scores and evidence are
# requested the same way. It is recorded on every ScreeningResult and one
# review must not mix levels (see screening.check_screening_setup).
STRICTNESS_LEVELS: Dict[str, Dict[str, str]] = {
    "strict": {
        "label": "Strict (precision first)",
        "summary": "Include only with explicit evidence for every criterion; Maybe is rare and reserved for "
                   "abstracts that are highly ambiguous yet strongly suggestive. Smallest review queue, "
                   "highest risk of missing borderline papers.",
        "rules": (
            "2. Favor precision over sensitivity. Do NOT assume relevance without explicit evidence.\n"
            "3. If the paper does NOT explicitly meet ALL the required inclusion criteria, output \"Exclude\".\n"
            "4. \"Maybe\" should ONLY be used if the abstract is highly ambiguous but suggests strong potential "
            "evidence for the criteria."),
    },
    "balanced": {
        "label": "Balanced",
        "summary": "Include with explicit evidence for every criterion; Maybe whenever the abstract is unclear "
                   "or silent on a criterion that the full text could plausibly satisfy; Exclude when a criterion "
                   "is clearly not met.",
        "rules": (
            "2. Require explicit evidence for \"Include\": every criterion must be clearly met in the title or abstract.\n"
            "3. Output \"Exclude\" when at least one criterion is clearly NOT met or the paper is off-topic.\n"
            "4. Output \"Maybe\" when the abstract is ambiguous or silent about a criterion but the paper could "
            "plausibly satisfy it on full-text reading. Do not guess: uncertainty means \"Maybe\", not \"Include\"."),
    },
    "lenient": {
        "label": "Lenient (recall first)",
        "summary": "Favor sensitivity: Exclude only papers that are clearly off-topic or clearly fail a criterion; "
                   "everything that might qualify becomes Maybe for human review. Largest review queue, fewest "
                   "missed papers.",
        "rules": (
            "2. Favor sensitivity over precision: it is worse to miss a relevant paper than to pass a doubtful one "
            "to the human reviewer.\n"
            "3. Output \"Exclude\" ONLY when the paper is clearly off-topic or clearly fails at least one criterion.\n"
            "4. Output \"Include\" when every criterion is explicitly met; otherwise, whenever the paper might "
            "plausibly meet the criteria, output \"Maybe\" so a human can check the full text."),
    },
}
DEFAULT_STRICTNESS = "strict"

SCREENING_PROMPT_TEMPLATE = """
You are a high-sensitivity screening assistant for an academic systematic review.
Your task is to evaluate if the following research paper meets the inclusion criteria.

**Paper Details**
Title: {title}
Abstract: {abstract}

{criteria_descriptions}
**Instructions**
1. Evaluate the inclusion criteria strictly and separately.
{decision_rules}
5. If the decision is "Exclude" or "Maybe", specify which inclusion criteria are not met in the "unmet_criteria" field (e.g. "IC2", "IC2+IC3", or "None").

**Output Format (JSON)**
Respond ONLY with a valid JSON object matching this schema:
{json_schema}
"""

def normalize_strictness(level: str) -> str:
    level = (level or "").strip().lower()
    return level if level in STRICTNESS_LEVELS else DEFAULT_STRICTNESS


def generate_prompt(title: str, abstract: str, criteria: Dict[str, Any],
                    strictness: str = DEFAULT_STRICTNESS) -> str:
    """
    Generates the final prompt string dynamically based on the provided criteria
    and the decision strictness level (see ``STRICTNESS_LEVELS``).
    """
    decision_rules = STRICTNESS_LEVELS[normalize_strictness(strictness)]["rules"]
    criteria_sections = []
    schema_criteria_fields = {}
    
    for key, c in criteria.items():
        section = f"**{key}: {c.get('name')}**\n"
        section += f"- Definition: {c.get('definition')}\n"
        section += f"- Signals: {c.get('signals')}\n"
        section += f"- Negative Indicators: {c.get('negative_indicators')}\n"
        criteria_sections.append(section)
        
        schema_criteria_fields[key] = {
            "score": "float (0.0 to 1.0)",
            "evidences": ["list of quoted keywords/phrases"],
            "rationale": "explanation"
        }
        
    criteria_descriptions = "\n".join(criteria_sections)
    
    schema = {
        "decision": "Include | Exclude | Maybe",
        "unmet_criteria": f"e.g., {'+'.join(criteria.keys())} or None",
        "notes": "Any ambiguities or edge cases"
    }
    schema.update(schema_criteria_fields)
    
    json_schema = json.dumps(schema, indent=2)
    # Removing the quotes from instructions so the LLM doesn't just output strings for objects
    json_schema = json_schema.replace('"Include | Exclude | Maybe"', '"Include" | "Exclude" | "Maybe"')
    json_schema = json_schema.replace('"float (0.0 to 1.0)"', 'float (0.0 to 1.0)')
    json_schema = json_schema.replace('"[list of quoted keywords/phrases]"', '["list of quoted keywords/phrases"]')
    json_schema = json_schema.replace('"explanation"', '"explanation string"')
    
    return SCREENING_PROMPT_TEMPLATE.format(
        title=title,
        abstract=abstract,
        criteria_descriptions=criteria_descriptions,
        json_schema=json_schema
    , decision_rules=decision_rules)
