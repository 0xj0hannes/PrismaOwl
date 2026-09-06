import json
import os
import time
import logging
from typing import List, Dict, Optional
from .models import Record, ScreeningResult, CriterionResult
from .config import load_config
from .llm import (LLMClient, LLMError, parse_json_text, build_client,
                  provider_settings, missing_key_message,
                  ScreeningModelError, screening_model, check_resolved_model)

from .prompts import generate_prompt

config = load_config()


def reload_config():
    """Re-read .env and criteria.json into the module-level ``config`` dict
    (mutated in place so anything holding a reference sees the update).
    Called by the web UI after the user edits criteria."""
    config.clear()
    config.update(load_config())
    return config


# Configure Logging
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/screening.log",
    level=logging.DEBUG, # Set to DEBUG to capture detailed prompts and responses
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ScreeningLogger")

# Configure the LLM client for the active provider (OrcaRouter or Gemini).
# None when no key is configured; screen_record then fails fast with a clear
# note instead of crashing at import time.
client: Optional[LLMClient] = build_client(config)

SYSTEM_MESSAGE = ("You are a meticulous systematic-review screening assistant. "
                  "Always answer with a single valid JSON object and nothing else.")


def screen_record(record: Record) -> ScreeningResult:
    try:
        model_name = screening_model(config)
    except ScreeningModelError as e:
        logger.error(f"Screening model not pinned: {e}")
        return ScreeningResult(
            record_id=record.id,
            decision="Maybe",
            notes=f"Failed after 0 attempts. Last error: [model_not_pinned] {e}",
        )
    pin_check = provider_settings(config)["provider"] == "orcarouter"
    criteria = config.get("CRITERIA", {})
    prompt = generate_prompt(title=record.title, abstract=record.abstract, criteria=criteria)

    max_retries = config.get("MAX_RETRIES", 3)
    last_error = ""

    logger.info(f"--- Starting screening for Record ID: {record.id} (model: {model_name}) ---")
    logger.debug(f"[PROMPT SENT]\n{prompt}\n[END PROMPT]")

    if client is None:
        msg = missing_key_message(config)
        logger.error(f"{msg}; cannot screen.")
        return ScreeningResult(
            record_id=record.id,
            decision="Maybe",
            notes=f"Failed after 0 attempts. Last error: 401 {msg}",
        )

    attempt = 0
    transient_attempts = 0

    while True:
        try:
            logger.info(f"Attempt {attempt+1}/{max_retries} (Transient Retries: {transient_attempts})...")
            response = client.chat(
                [{"role": "system", "content": SYSTEM_MESSAGE},
                 {"role": "user", "content": prompt}],
                model=model_name,
                json_mode=True,
            )

            logger.debug(f"[RAW RESPONSE model={response.model_label} cached={response.cached}]\n"
                         f"{response.text}\n[END RESPONSE]")

            # Reproducibility guard: the router must have used the pinned model.
            if pin_check and not check_resolved_model(model_name, response.resolved_model):
                raise LLMError(
                    f"OrcaRouter answered with '{response.resolved_model}' instead of the "
                    f"pinned screening model '{model_name}'. The result was discarded.",
                    code="model_substituted")

            # Parse JSON
            result_json = parse_json_text(response.text)

            parsed_criteria = {}
            for key in criteria.keys():
                c_data = result_json.get(key, {})
                if not isinstance(c_data, dict):
                    c_data = {}
                parsed_criteria[key] = CriterionResult(
                    score=float(c_data.get("score", 0.0) or 0.0),
                    evidences=c_data.get("evidences", []) or [],
                    rationale=c_data.get("rationale", "") or ""
                )

            logger.info(f"Record {record.id} mapped successfully. Decision: {result_json.get('decision')}")

            return ScreeningResult(
                record_id=record.id,
                decision=result_json.get("decision", "Maybe"),
                criteria=parsed_criteria,
                unmet_criteria=str(result_json.get("unmet_criteria", "") or ""),
                notes=str(result_json.get("notes", "") or ""),
                timestamp=str(os.times()),
                model_version=response.model_label
            )

        except Exception as e:
            last_error = str(e)

            if isinstance(e, LLMError):
                is_fatal = e.fatal
                is_transient = e.transient and not is_fatal
            else:
                # JSON / parsing / unexpected errors: retry a bounded number of times.
                is_fatal = False
                is_transient = False

            if is_transient:
                logger.warning(f"Transient API Error on Record {record.id}: {last_error}")
            else:
                logger.error(f"API Error on Attempt {attempt+1}/{max_retries} for Record {record.id}: {last_error}", exc_info=True)

            print(f"  API Error: {last_error}. Retrying...")

            if is_fatal:
                break  # Wrong key, no credits, unknown model: nothing to retry.
            if is_transient:
                # Wait indefinitely for transient errors, but cap the sleep time backoff
                capped_attempt = min(transient_attempts, 6) # Max 15 * 64 = 960 seconds ~ 16 minutes
                sleep_time = 15 * (2 ** capped_attempt)
                logger.info(f"Sleeping for {sleep_time} seconds before retrying infinitely...")
                time.sleep(sleep_time)
                transient_attempts += 1
                continue
            else:
                if attempt < max_retries - 1:
                    time.sleep(5)
                    attempt += 1
                    continue
                else:
                    break # Give up on persistent non-transient errors

    logger.error(f"Failed to screen Record {record.id} after {attempt + 1} attempts. Last error: {last_error}")
    return ScreeningResult(
        record_id=record.id,
        decision="Maybe",
        notes=f"Failed after {attempt + 1} attempts. Last error: {last_error}"
    )

def is_model_unavailable(notes: str) -> bool:
    """True if a failed result's notes indicate the configured model itself is
    gone (404 / unknown model id), so retrying other records with it is pointless."""
    if "Failed after" not in notes:
        return False
    lowered = notes.lower()
    return "404" in notes or "not_found" in lowered or "not found" in lowered \
        or "no longer available" in lowered or "model_not_found" in lowered


def is_billing_or_auth_error(notes: str) -> bool:
    """True if a failed result's notes indicate the API key is rejected or the
    OrcaRouter account is out of credits."""
    if "Failed after" not in notes:
        return False
    lowered = notes.lower()
    return "401" in notes or "402" in notes or "403" in notes \
        or "insufficient_quota" in lowered or "insufficient_user_quota" in lowered \
        or "out of credits" in lowered or "invalid_api_key" in lowered \
        or "api_key is not set" in lowered


def is_model_pinning_error(notes: str) -> bool:
    """True if a failed result's notes say the screening model is not pinned
    to one concrete model, or that the router substituted another model."""
    if "Failed after" not in notes:
        return False
    return "model_not_pinned" in notes or "model_substituted" in notes


def is_fatal_error(notes: str) -> bool:
    """Any failure that should abort the whole batch (see the helpers above)."""
    return is_model_unavailable(notes) or is_billing_or_auth_error(notes) \
        or is_model_pinning_error(notes)


MODEL_UNAVAILABLE_HINT = (
    "The configured model appears to be unavailable or deprecated. Set MODEL_NAME (or "
    "MODEL_SCREENING) in .env to a model id listed by 'python3 test_llm.py' "
    "(e.g. orcarouter/auto, or gemini-3.5-flash when LLM_PROVIDER=gemini)."
)

BILLING_HINT = (
    "OrcaRouter rejected the request: check ORCA_API_KEY in .env and your credit "
    "balance at https://www.orcarouter.ai/console/billing."
)

GEMINI_BILLING_HINT = (
    "Google Gemini rejected the request: check GEMINI_API_KEY in .env "
    "(https://aistudio.google.com/) and your quota / billing status."
)


PINNING_HINT = (
    "Screening runs on one fixed model for reproducibility. Set MODEL_SCREENING in "
    ".env to a concrete OrcaRouter model id (e.g. anthropic/claude-sonnet-5, or a free "
    "one such as deepseek/deepseek-v4-flash-free), not a meta-model such as "
    "orcarouter/auto; results from a substituted model are discarded."
)


def fatal_error_hint(notes: str) -> str:
    if is_model_pinning_error(notes):
        return PINNING_HINT
    if is_model_unavailable(notes):
        return MODEL_UNAVAILABLE_HINT
    if provider_settings(config)["provider"] == "gemini":
        return GEMINI_BILLING_HINT
    return BILLING_HINT

def existing_screening_models(results) -> Dict[str, int]:
    """Count successful results per ``model_version`` (failed ones ignored).
    Accepts ``ScreeningResult`` objects or their ``model_dump()`` dicts."""
    counts: Dict[str, int] = {}
    for res in (results.values() if isinstance(results, dict) else results):
        if isinstance(res, ScreeningResult):
            notes, model = res.notes, res.model_version
        else:
            notes, model = res.get("notes", "") or "", res.get("model_version", "") or ""
        if "Failed after" in notes or not model:
            continue
        counts[model] = counts.get(model, 0) + 1
    return counts


def check_screening_setup(already_screened=None) -> str:
    """Validate the screening model before a batch starts and return its id.

    Raises ``ScreeningModelError`` if the model is a routing meta-model (on
    OrcaRouter) or if results that already exist were produced by a different
    model - mixing models inside one screening pass would break the audit
    trail. Reset the results (or restore the previous MODEL_SCREENING) to
    proceed.
    """
    model = screening_model(config)
    others = {m: n for m, n in existing_screening_models(already_screened or {}).items()
              if not check_resolved_model(model, m)}
    if others:
        listing = ", ".join(f"{m} ({n} records)" for m, n in sorted(others.items()))
        raise ScreeningModelError(
            f"Existing screening results were produced by {listing}, but the screening "
            f"model is now '{model}'. All records of one review must be screened by the "
            f"same model: restore MODEL_SCREENING to the previous model, or reset the "
            f"screening results to re-screen everything with '{model}'.")
    return model


def batch_screen(records: List[Record], already_screened: Dict[str, ScreeningResult] = None):
    """
    Screens records using LLM. Returns a generator that yields (record_id, result).
    This allows the caller to save progress incrementally.

    Raises ``ScreeningModelError`` (before yielding anything) if the screening
    model is not pinned or differs from the one used for existing results.
    """
    if already_screened is None:
        already_screened = {}

    model = check_screening_setup(already_screened)
    print(f"Screening {len(records)} records with pinned model {model}...")

    for i, record in enumerate(records):
        if record.id in already_screened:
            # Skip if we already have a valid decision (not a failure from previous run)
            existing = already_screened[record.id]
            if "Failed after" not in existing.notes:
                # No need to yield if it's already in already_screened and valid
                continue

        print(f"Processing {i}/{len(records)}...")

        result = screen_record(record)
        yield record.id, result

        # Check for fatal errors that should stop the batch
        if is_fatal_error(result.notes):
            print(f"\n[FATAL] Screening cannot continue at record {i}. Stopping batch.")
            print(f"  {fatal_error_hint(result.notes)}")
            break
        if "Quota exceeded" in result.notes or "429" in result.notes:
            print(f"\n[FATAL] API limit reached at record {i}. Stopping batch.")
            break
        if "exhausted" in result.notes.lower():
            print(f"\n[FATAL] Resource exhausted at record {i}. Stopping batch.")
            break
