"""
LLM client shared by every LLM feature in PrismaOwl.

Two providers are supported, selected by ``LLM_PROVIDER`` in .env (or
auto-detected from whichever API key is present):

* ``orcarouter`` (default) - https://api.orcarouter.ai/v1, an OpenAI-compatible
  gateway that routes each request to one of 100+ upstream models.
* ``gemini`` - the Google Gemini Developer API, reached through its
  OpenAI-compatible endpoint (``.../v1beta/openai``) with a ``GEMINI_API_KEY``
  from Google AI Studio. No vendor SDK is needed.

Both speak the same ``/chat/completions`` dialect, so one plain-``requests``
client serves both. Talking HTTP directly also lets us read the routing
headers OrcaRouter adds (``x-orca-resolved-model``, ``x-orca-cache``) and log
which concrete model actually handled each call - important for auditability
in a systematic review.

Model selection is per task (see ``model_for``): ``MODEL_NAME`` is the global
default (``orcarouter/auto`` = adaptive routing) and ``MODEL_SCREENING``,
``MODEL_QUERY``, ``MODEL_CRITERIA`` and ``MODEL_CHAT`` override it for the
respective feature, so cheap routing can be used for bulk screening and a
stronger model for strategy design or chat.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from .config import load_config, PROVIDERS, DEFAULT_PROVIDER, resolve_provider

# All LLM traffic (prompts + raw responses) goes to logs/screening.log for
# auditability. basicConfig is a no-op if the root logger is already set up.
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/screening.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ScreeningLogger")

DEFAULT_BASE_URL = PROVIDERS[DEFAULT_PROVIDER]["base_url"]
DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER]["default_model"]
USER_AGENT = "PrismaOwl/1.0 (https://github.com/0xj0hannes/PrismaOwl)"

TASKS = ("screening", "query", "criteria", "chat")

# HTTP statuses worth waiting for: rate limits and upstream hiccups.
TRANSIENT_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
# Statuses / error codes that will not fix themselves by retrying.
FATAL_STATUSES = {401, 402, 403, 404}
FATAL_CODES = {"insufficient_quota", "insufficient_user_quota", "invalid_api_key",
               "model_not_found", "invalid_request_error",
               # raised locally when the router answered with a model other
               # than the one pinned for screening (see check_resolved_model)
               "model_substituted",
               # Gemini reports gRPC-style status strings in error.status
               "NOT_FOUND", "PERMISSION_DENIED", "UNAUTHENTICATED"}


class LLMError(RuntimeError):
    """Raised when the LLM provider could not produce a usable answer."""

    def __init__(self, message: str, status: Optional[int] = None, code: str = "",
                 error_type: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code or ""
        self.error_type = error_type or ""

    @property
    def transient(self) -> bool:
        if self.status is None:  # network-level failure
            return True
        return self.status in TRANSIENT_STATUSES

    @property
    def fatal(self) -> bool:
        return (self.status in FATAL_STATUSES) or (self.code in FATAL_CODES) \
            or (self.error_type in FATAL_CODES)

    def __str__(self) -> str:
        base = super().__str__()
        prefix = f"HTTP {self.status} " if self.status is not None else ""
        code = f"[{self.code}] " if self.code else ""
        return f"{prefix}{code}{base}"


@dataclass
class LLMResponse:
    text: str
    model: str                       # model id we asked for
    resolved_model: str = ""         # concrete upstream chosen by the router
    usage: Dict[str, Any] = field(default_factory=dict)
    cached: bool = False
    request_id: str = ""

    @property
    def model_label(self) -> str:
        """Best identifier of the model that produced the text, for audit logs."""
        return self.resolved_model or self.model


class LLMClient:
    """OpenAI-compatible chat-completions client (OrcaRouter or Gemini)."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 300.0,
                 provider: str = DEFAULT_PROVIDER):
        self.provider = provider if provider in PROVIDERS else DEFAULT_PROVIDER
        if not api_key:
            raise LLMError(f"{PROVIDERS[self.provider]['key_env']} is not set in .env")
        self.api_key = api_key
        self.base_url = (base_url or PROVIDERS[self.provider]["base_url"]).rstrip("/")
        self.timeout = timeout

    @property
    def provider_label(self) -> str:
        return PROVIDERS[self.provider]["label"]

    # -- low level ---------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT}

    @staticmethod
    def _raise_for_error(resp: requests.Response) -> None:
        if resp.status_code < 400:
            return
        message, code, etype = resp.text[:500], "", ""
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict):
                message = err.get("message", message)
                code = str(err.get("code", "") or "")
                # OpenAI-style "type"; Gemini uses a gRPC "status" string instead.
                etype = str(err.get("type", "") or err.get("status", "") or "")
            elif isinstance(err, str):
                message = err
        except ValueError:
            pass
        raise LLMError(message, status=resp.status_code, code=code, error_type=etype)

    def list_models(self) -> List[Dict[str, Any]]:
        try:
            resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMError(f"Network error: {e}") from e
        self._raise_for_error(resp)
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    def chat(self, messages: List[Dict[str, str]], model: str = DEFAULT_MODEL,
             json_mode: bool = False, temperature: float = 0.0,
             max_tokens: Optional[int] = None, extra: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """One OpenAI-style chat completion. Raises ``LLMError`` on any failure."""
        body: Dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if extra:
            body.update(extra)
        try:
            resp = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                                 json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise LLMError(f"Network error: {e}") from e
        self._raise_for_error(resp)
        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Malformed response from {self.provider_label}: {resp.text[:300]}",
                           status=resp.status_code) from e
        if isinstance(text, list):  # some providers return content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        return LLMResponse(
            text=text,
            model=model,
            resolved_model=resp.headers.get("x-orca-resolved-model") or data.get("model") or "",
            usage=data.get("usage") or {},
            cached=(resp.headers.get("x-orca-cache") or "").upper() == "HIT",
            request_id=resp.headers.get("x-request-id") or data.get("id") or "",
        )


# --------------------------------------------------------------------------
# Module-level convenience layer
# --------------------------------------------------------------------------

# Backwards-compatible name from when OrcaRouter was the only provider.
OrcaClient = LLMClient


def provider_for(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Active provider name for a config dict (``LLM_PROVIDER``, else detected
    from the keys present, else the default)."""
    cfg = cfg if cfg is not None else load_config()
    if cfg.get("LLM_PROVIDER"):
        return cfg["LLM_PROVIDER"]
    return resolve_provider("", cfg.get("ORCA_API_KEY") or "", cfg.get("GEMINI_API_KEY") or "")


def provider_settings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolved ``{provider, label, key_env, api_key, base_url, default_model,
    console}`` for the active provider."""
    cfg = cfg if cfg is not None else load_config()
    name = provider_for(cfg)
    spec = PROVIDERS[name]
    api_key = cfg.get("LLM_API_KEY") if cfg.get("LLM_PROVIDER") else None
    if api_key is None:
        api_key = cfg.get(spec["key_env"])
    base_url = cfg.get("LLM_BASE_URL") if cfg.get("LLM_PROVIDER") else None
    if not base_url:
        base_url = cfg.get(spec["base_url_env"]) or spec["base_url"]
    return {"provider": name, "label": spec["label"], "key_env": spec["key_env"],
            "api_key": api_key or "", "base_url": base_url,
            "default_model": spec["default_model"], "console": spec["console"]}


def missing_key_message(cfg: Optional[Dict[str, Any]] = None) -> str:
    ps = provider_settings(cfg)
    return f"{ps['key_env']} is not set in .env"


def build_client(cfg: Optional[Dict[str, Any]] = None) -> Optional[LLMClient]:
    """Construct a client for the active provider, or ``None`` when its API key
    is missing (callers then fail with a clear note instead of crashing)."""
    cfg = cfg if cfg is not None else load_config()
    ps = provider_settings(cfg)
    if not ps["api_key"]:
        return None
    return LLMClient(ps["api_key"], ps["base_url"], timeout=cfg.get("LLM_TIMEOUT", 300.0),
                     provider=ps["provider"])


_client: Optional[LLMClient] = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        cfg = load_config()
        client = build_client(cfg)
        if client is None:
            raise LLMError(missing_key_message(cfg), status=401)
        _client = client
    return _client


def reset_client() -> None:
    """Drop the cached client (e.g. after the API key changed)."""
    global _client
    _client = None


def model_for(task: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Resolve which model id to use for a task: ``MODEL_<TASK>``, else
    ``MODEL_NAME``, else the active provider's default model."""
    cfg = cfg if cfg is not None else load_config()
    specific = cfg.get(f"MODEL_{task.upper()}") if task else None
    return specific or cfg.get("MODEL_NAME") or PROVIDERS[provider_for(cfg)]["default_model"]


# --------------------------------------------------------------------------
# Screening model pinning (reproducibility)
# --------------------------------------------------------------------------
#
# Title/abstract screening is the step whose decisions end up in the PRISMA
# report, so it must be reproducible: every record has to be judged by the
# same, explicitly named model. OrcaRouter's meta-models ("orcarouter/auto",
# "orcarouter/free", "orcarouter/fusion-*", ...) pick a different upstream per
# request, which is fine for drafting queries or chatting but not for
# screening. When the provider is OrcaRouter the screening model therefore has
# to be a concrete "vendor/model" id, and the model the router reports back is
# checked against it after every call.

META_MODEL_PREFIX = "orcarouter/"


class ScreeningModelError(ValueError):
    """The screening model is not pinned to one concrete model."""


def is_meta_model(model: str) -> bool:
    """True for OrcaRouter meta-models that route dynamically."""
    return (model or "").strip().lower().startswith(META_MODEL_PREFIX)


def screening_model(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Model id used for screening (``MODEL_SCREENING`` else ``MODEL_NAME``).

    Raises ``ScreeningModelError`` when the provider is OrcaRouter and the id
    is a routing meta-model, because screening must run on one fixed model.
    """
    cfg = cfg if cfg is not None else load_config()
    model = model_for("screening", cfg)
    if provider_for(cfg) == "orcarouter" and is_meta_model(model):
        raise ScreeningModelError(
            f"Screening must run on one fixed model for reproducibility, but "
            f"'{model}' is an OrcaRouter meta-model that routes each request to a "
            f"different upstream. Set MODEL_SCREENING in .env to a concrete id such "
            f"as 'anthropic/claude-sonnet-5' (run 'python3 test_llm.py --models' to "
            f"list them). Meta-models remain fine for MODEL_QUERY, MODEL_CRITERIA and "
            f"MODEL_CHAT.")
    return model


def _norm_model_id(model: str) -> str:
    m = (model or "").strip().lower()
    return m[len("models/"):] if m.startswith("models/") else m


def check_resolved_model(requested: str, resolved: str) -> bool:
    """Whether the model the provider reports having used is the one we asked
    for. Tolerates an empty report (nothing to check) and version suffixes
    (``anthropic/claude-sonnet-5`` vs ``anthropic/claude-sonnet-5-20260301``)."""
    req, res = _norm_model_id(requested), _norm_model_id(resolved)
    if not req or not res:
        return True
    return req == res or res.startswith(req) or req.startswith(res)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def parse_json_text(text: str) -> Dict[str, Any]:
    """Parse a JSON object out of model output, tolerating code fences and
    leading/trailing prose (reasoning models sometimes add both)."""
    text = _strip_code_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_json(prompt: str, task: str = "", retries: int = 2,
                  system_instruction: Optional[str] = None) -> Dict[str, Any]:
    """Send ``prompt`` in JSON mode and return the parsed object.

    Retries on malformed JSON and transient router errors, then raises
    ``LLMError`` with the last failure so callers can show it to the user.
    """
    client = get_client()
    model = model_for(task)
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    logger.debug(f"[LLM JSON PROMPT model={model} task={task}]\n{prompt}\n[END PROMPT]")
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat(messages, model=model, json_mode=True)
            logger.debug(f"[LLM JSON RESPONSE model={resp.model_label} cached={resp.cached}]\n"
                         f"{resp.text}\n[END RESPONSE]")
            return parse_json_text(resp.text)
        except json.JSONDecodeError as e:
            last_error = LLMError(f"Model returned invalid JSON: {e}")
        except LLMError as e:
            last_error = e
            if e.fatal or not e.transient:
                break
        logger.warning(f"LLM JSON attempt {attempt + 1} failed: {last_error}")
        time.sleep(2 * (attempt + 1))
    raise last_error if isinstance(last_error, LLMError) else LLMError(str(last_error))


def generate_chat(messages: List[Dict[str, str]], system_instruction: str,
                  task: str = "chat") -> str:
    """Multi-turn text generation.

    ``messages`` is a list of ``{"role": "user" | "assistant", "content": str}``
    in chronological order; the last message must be from the user.
    """
    if not messages or messages[-1].get("role") != "user":
        raise LLMError("The last chat message must come from the user.")
    convo = [{"role": "system", "content": system_instruction}]
    for m in messages:
        role = "assistant" if m.get("role") == "assistant" else "user"
        convo.append({"role": role, "content": m.get("content", "")})

    model = model_for(task)
    logger.debug(f"[LLM CHAT model={model}] {len(messages)} turns, system prompt "
                 f"{len(system_instruction)} chars")
    resp = get_client().chat(convo, model=model, temperature=0.2)
    logger.debug(f"[LLM CHAT RESPONSE model={resp.model_label}]\n{resp.text}\n[END RESPONSE]")
    return resp.text
