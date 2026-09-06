import os
import asyncio
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import tempfile

from src.ingestion import load_bibtex
from src.deduplication import deduplicate_records
from src.models import Record, ScreeningResult, Dataset
from src.screening import screen_record, is_fatal_error, fatal_error_hint, check_screening_setup
from src.reporting import generate_report
from src.db import init_db, save_record, save_screening_result, get_all_records, get_all_screening_results, get_unique_records, clear_screening_results
from src.config import (load_config, save_criteria, load_search_strategy, save_search_strategy,
                        update_env, PROVIDERS, EDITABLE_SETTINGS, SECRET_SETTINGS)
from src import screening as screening_module
from src import llm as llm_module
from src.llm import LLMError, ScreeningModelError, LLMClient, model_for, provider_settings, TASKS
from src.search_strategy import generate_strategy, normalize_strategy, DATABASES
from src.harvest import harvest_to_file, list_sources, HarvestError, HARVEST_DIR
from src.criteria_assist import generate_criteria, validate_criteria
from src.chat import ask as chat_ask, select_records, SCOPES
import json
import threading
import uuid
from datetime import datetime

app = FastAPI(title="PrismaOwl")

# Initialize database
init_db()

# Serve static files for the UI
os.makedirs("static/css", exist_ok=True)
os.makedirs("static/js", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html", "r") as f:
        return f.read()

@app.get("/api/criteria")
async def get_criteria():
    config = load_config()
    return config.get("CRITERIA", {})


@app.put("/api/criteria")
async def put_criteria(request: Request):
    """Replace criteria.json with the editor's content. The screening module's
    cached config is refreshed so the next screened record uses the new
    criteria; results screened under the old criteria are left untouched."""
    if is_screening_running:
        return JSONResponse({"error": "Stop screening before editing criteria."}, status_code=409)
    try:
        criteria = validate_criteria(await request.json())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    save_criteria(criteria)
    screening_module.reload_config()
    return {"status": "success", "criteria": criteria}


@app.post("/api/criteria/generate")
async def generate_criteria_endpoint(request: Request):
    """Draft or refine criteria with the LLM. Nothing is saved: the proposal is
    returned for the user to edit and explicitly save via PUT /api/criteria."""
    data = await request.json()
    topic = (data.get("topic") or "").strip() or load_search_strategy().get("research_question", "")
    current = data.get("current") or None
    try:
        proposed = await asyncio.to_thread(
            generate_criteria, topic, current, data.get("feedback", ""), data.get("count"))
    except (LLMError, ValueError) as e:
        return JSONResponse({"error": getattr(e, "user_message", str(e))}, status_code=400)
    return {"criteria": proposed}


@app.get("/api/llm")
async def llm_info():
    """Active LLM provider and which model each task will use (from .env),
    for the UI footer."""
    cfg = load_config()
    ps = provider_settings(cfg)
    return {
        "provider": ps["label"],
        "provider_id": ps["provider"],
        "key_env": ps["key_env"],
        "base_url": ps["base_url"],
        "configured": bool(ps["api_key"]),
        "models": {"default": model_for("", cfg), **{t: model_for(t, cfg) for t in TASKS}},
    }


# ---------------------------------------------------------------------------
# Settings (web editor for .env)
# ---------------------------------------------------------------------------

def _secret_state(value):
    """Never send a secret to the browser: only whether it is set and a hint."""
    value = value or ""
    return {"set": bool(value), "hint": ("…" + value[-4:]) if len(value) >= 8 else ""}


@app.get("/api/settings")
async def get_settings():
    cfg = load_config()
    ps = provider_settings(cfg)
    return {
        "providers": {k: {"label": v["label"], "key_env": v["key_env"], "default_model": v["default_model"],
                          "base_url": v["base_url"], "console": v["console"]} for k, v in PROVIDERS.items()},
        "active_provider": ps["provider"],
        "values": {
            "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "") or "",
            "ORCA_BASE_URL": cfg.get("ORCA_BASE_URL") or "",
            "GEMINI_BASE_URL": cfg.get("GEMINI_BASE_URL") or "",
            # Raw MODEL_NAME (may be empty = provider default), not the resolved one.
            "MODEL_NAME": os.getenv("MODEL_NAME", "") or "",
            **{f"MODEL_{t.upper()}": cfg.get(f"MODEL_{t.upper()}") or "" for t in TASKS},
            "MAX_RETRIES": cfg.get("MAX_RETRIES"),
            "LLM_TIMEOUT": cfg.get("LLM_TIMEOUT"),
            "OPENALEX_EMAIL": cfg.get("OPENALEX_EMAIL") or "",
        },
        "secrets": {k: _secret_state(os.getenv(k)) for k in SECRET_SETTINGS},
        "resolved_models": {"default": model_for("", cfg), **{t: model_for(t, cfg) for t in TASKS}},
        "free_screening_default": llm_module.DEFAULT_FREE_SCREENING_MODEL,
    }


@app.put("/api/settings")
async def put_settings(request: Request):
    """Write settings to .env. Non-secret fields are written as given; a secret
    is kept when ``null``/absent, cleared when ``""`` and replaced otherwise."""
    if is_screening_running:
        return JSONResponse({"error": "Stop screening before changing settings."}, status_code=409)
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse({"error": "Expected a JSON object."}, status_code=400)

    values = {}
    errors = []
    for key in EDITABLE_SETTINGS:
        if key not in data or data[key] is None:
            continue
        raw = data[key]
        val = str(raw).strip() if not isinstance(raw, str) else raw.strip()
        if key == "LLM_PROVIDER" and val and val not in PROVIDERS:
            errors.append(f"LLM_PROVIDER must be one of: {', '.join(PROVIDERS)} (or empty for auto-detect).")
        elif key == "MAX_RETRIES" and val:
            if not val.isdigit() or int(val) < 1:
                errors.append("MAX_RETRIES must be a whole number of at least 1.")
        elif key == "LLM_TIMEOUT" and val:
            try:
                if float(val) <= 0:
                    raise ValueError
            except ValueError:
                errors.append("LLM_TIMEOUT must be a positive number of seconds.")
        elif key.endswith("_BASE_URL") and val and not val.startswith(("http://", "https://")):
            errors.append(f"{key} must start with http:// or https://.")
        if "\n" in val or "\r" in val:
            errors.append(f"{key} must be a single line.")
        values[key] = val
    if errors:
        return JSONResponse({"error": " ".join(errors)}, status_code=400)
    if not values:
        return JSONResponse({"error": "Nothing to save."}, status_code=400)

    update_env(values)
    screening_module.reload_config()
    llm_module.reset_client()
    return {"status": "success", "written": sorted(values)}


def _client_for(provider: str):
    cfg = load_config()
    if provider:
        if provider not in PROVIDERS:
            raise LLMError(f"Unknown provider '{provider}'.", status=400)
        # Drop the values resolved for the active provider so the override
        # really uses this provider's own key and base URL.
        cfg = {**cfg, "LLM_PROVIDER": provider, "LLM_API_KEY": None, "LLM_BASE_URL": None}
    ps = provider_settings(cfg)
    if not ps["api_key"]:
        raise LLMError(f"{ps['key_env']} is not set. Save the key first.", status=401)
    return ps, LLMClient(ps["api_key"], ps["base_url"], timeout=min(cfg.get("LLM_TIMEOUT", 60.0), 60.0),
                         provider=ps["provider"])


@app.get("/api/settings/models")
async def settings_models(provider: str = ""):
    """Model ids the saved key of ``provider`` (default: the active one) can use."""
    try:
        ps, client = _client_for(provider)
        models = await asyncio.to_thread(client.list_models)
    except LLMError as e:
        return JSONResponse({"error": e.user_message}, status_code=400)
    ids = sorted({str(m.get("id", "")) for m in models if isinstance(m, dict) and m.get("id")})
    return {"provider": ps["provider"], "label": ps["label"], "models": ids}


@app.post("/api/settings/test")
async def settings_test(request: Request):
    """Check that the saved key for a provider is accepted (lists its models)."""
    data = await request.json() if int(request.headers.get("content-length") or 0) > 0 else {}
    provider = (data or {}).get("provider") or ""
    try:
        ps, client = _client_for(provider)
        models = await asyncio.to_thread(client.list_models)
    except LLMError as e:
        return JSONResponse({"error": e.user_message}, status_code=400)
    return {"ok": True, "provider": ps["provider"], "label": ps["label"],
            "base_url": ps["base_url"], "model_count": len(models)}


# ---------------------------------------------------------------------------
# Search strategy (LLM query builder) + database harvesting
# ---------------------------------------------------------------------------

@app.get("/api/search-strategy")
async def get_search_strategy():
    strategy = load_search_strategy()
    return {"strategy": normalize_strategy(strategy) if strategy else None,
            "databases": {k: v["label"] for k, v in DATABASES.items()}}


@app.put("/api/search-strategy")
async def put_search_strategy(request: Request):
    strategy = normalize_strategy(await request.json())
    save_search_strategy(strategy)
    return {"status": "success", "strategy": strategy}


@app.post("/api/search-strategy/generate")
async def generate_search_strategy(request: Request):
    data = await request.json()
    try:
        strategy = await asyncio.to_thread(
            generate_strategy, data.get("topic", ""), data.get("current") or None, data.get("feedback", ""))
    except (LLMError, ValueError) as e:
        return JSONResponse({"error": getattr(e, "user_message", str(e))}, status_code=400)
    return {"strategy": strategy}


harvest_jobs = {}
harvest_lock = threading.Lock()


@app.get("/api/harvest/sources")
async def harvest_sources():
    return {"sources": list_sources()}


@app.post("/api/harvest")
async def start_harvest(request: Request):
    data = await request.json()
    source = data.get("source", "")
    query = (data.get("query") or "").strip()
    max_results = int(data.get("max_results") or 500)
    if not query:
        return JSONResponse({"error": "Query is empty."}, status_code=400)
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "source": source, "query": query, "max_results": max_results,
           "status": "running", "fetched": 0, "total": None, "error": None, "result": None,
           "started": datetime.now().isoformat(timespec="seconds")}
    with harvest_lock:
        harvest_jobs[job_id] = job

    def progress(n, total):
        job["fetched"], job["total"] = n, total

    def run():
        try:
            summary = harvest_to_file(source, query, max_results=max_results, progress=progress)
            summary["file"] = os.path.basename(summary["file"])
            job["result"] = summary
            job["fetched"] = summary["count"]
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001 - report any failure to the UI
            job["error"] = str(e)
            job["status"] = "failed"

    threading.Thread(target=run, daemon=True).start()
    return {"job": job}


@app.get("/api/harvest/jobs")
async def harvest_job_list():
    with harvest_lock:
        jobs = sorted(harvest_jobs.values(), key=lambda j: j["started"], reverse=True)
    return {"jobs": jobs}


def _harvest_path(name: str) -> str:
    safe = os.path.basename(name)
    if not safe.endswith(".bib"):
        raise ValueError("Not a .bib file")
    path = os.path.join(HARVEST_DIR, safe)
    if not os.path.isfile(path):
        raise FileNotFoundError(safe)
    return path


@app.get("/api/harvest/files")
async def harvest_files():
    os.makedirs(HARVEST_DIR, exist_ok=True)
    files = []
    for name in sorted(os.listdir(HARVEST_DIR), reverse=True):
        if name.endswith(".bib"):
            path = os.path.join(HARVEST_DIR, name)
            files.append({"name": name, "size": os.path.getsize(path),
                          "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")})
    return {"files": files}


@app.get("/api/harvest/download/{name}")
async def harvest_download(name: str):
    try:
        path = _harvest_path(name)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(path, media_type="application/x-bibtex", filename=os.path.basename(path))


@app.post("/api/harvest/ingest/{name}")
async def harvest_ingest(name: str):
    """Ingest a harvested .bib straight into the SQLite corpus (same
    deduplication path as /api/ingest)."""
    try:
        path = _harvest_path(name)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "Not found"}, status_code=404)
    new_records = load_bibtex(path)
    if not new_records:
        return JSONResponse({"error": "No records in file."}, status_code=400)
    existing_records = [Record(**r) for r in get_all_records()]
    deduped_all = deduplicate_records(existing_records + new_records)
    for rec in deduped_all:
        save_record(rec.model_dump())
    return {"status": "success", "uploaded": len(new_records), "total_unique_db": len(deduped_all)}


@app.delete("/api/harvest/files/{name}")
async def harvest_delete(name: str):
    try:
        path = _harvest_path(name)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "Not found"}, status_code=404)
    os.unlink(path)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Chat over screened results
# ---------------------------------------------------------------------------

@app.get("/api/chat/scope")
async def chat_scope_counts():
    records = get_unique_records()
    results = get_all_screening_results()
    return {"scopes": {scope: len(select_records(records, results, scope)) for scope in SCOPES}}


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    data = await request.json()
    messages = data.get("messages") or []
    scope = data.get("scope", "included")
    if scope not in SCOPES:
        return JSONResponse({"error": f"Unknown scope '{scope}'"}, status_code=400)
    if not messages or messages[-1].get("role") != "user":
        return JSONResponse({"error": "Send at least one user message."}, status_code=400)
    records = get_unique_records()
    results = get_all_screening_results()
    criteria = load_config().get("CRITERIA", {})
    try:
        out = await asyncio.to_thread(chat_ask, messages, records, results, criteria, scope)
    except LLMError as e:
        return JSONResponse({"error": e.user_message}, status_code=400)
    return out

from typing import List

@app.post("/api/ingest")
async def ingest_file(files: List[UploadFile] = File(...)):
    new_records = []
    
    for file in files:
        if not file.filename.endswith(".bib"):
            continue
        
        # Save temp
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bib") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            loaded = load_bibtex(tmp_path)
            new_records.extend(loaded)
        finally:
            os.unlink(tmp_path)
            
    if not new_records:
        return JSONResponse({"error": "No valid records found in uploaded files."}, status_code=400)
        
    # Pull existing records to perform global deduplication against new ones
    existing_dicts = get_all_records()
    existing_records = [Record(**r) for r in existing_dicts]
    
    all_records = existing_records + new_records
    deduped_all = deduplicate_records(all_records)
    
    # Save to SQLite (INSERT OR REPLACE will update duplicate tags if necessary)
    for rec in deduped_all:
        save_record(rec.model_dump())
        
    return {"status": "success", "uploaded": len(new_records), "total_unique_db": len(deduped_all)}

@app.get("/api/records")
async def get_records():
    return get_unique_records()

stop_screening_flag = False
is_screening_running = False

@app.get("/api/screen/status")
async def screen_status():
    all_res = get_all_screening_results()
    total = len(get_unique_records())
    # Pinned screening model + whether a batch may start with it (see
    # check_screening_setup); the UI shows the model and the reason if not.
    model_info = {"model": "", "model_ok": True, "model_error": ""}
    try:
        model_info["model"] = check_screening_setup(all_res)
    except ScreeningModelError as e:
        model_info.update(model_ok=False, model_error=str(e))
        try:
            model_info["model"] = model_for("screening")
        except Exception:  # noqa: BLE001
            pass
    return {"screened": len(all_res), "total": total, "is_running": is_screening_running,
            **model_info}

@app.post("/api/screen/start")
async def start_screening(background_tasks: BackgroundTasks):
    global stop_screening_flag, is_screening_running
    stop_screening_flag = False
    
    if is_screening_running:
        return {"status": "already started"}

    # Retrieve from DB
    records = get_unique_records()
    already_screened = get_all_screening_results()

    # Reproducibility: refuse to start unless screening is pinned to one
    # concrete model that matches the results already in the database.
    try:
        model = check_screening_setup(already_screened)
    except ScreeningModelError as e:
        return JSONResponse({"error": str(e)}, status_code=409)

    is_screening_running = True
    print(f"[Screening AI] Pinned screening model: {model}")
    
    def screen_task():
        global stop_screening_flag, is_screening_running
        try:
            recs_obj = [Record(**r) for r in records]
            for record in recs_obj:
                if stop_screening_flag:
                    break
                    
                if record.id in already_screened:
                    existing = already_screened[record.id]
                    if "Failed after" not in existing.get("notes", ""):
                        continue
                
                try:
                    print(f"[Screening AI] Analyzing: {record.id} -> '{record.title[:60]}...'")
                    res = screen_record(record)
                    scores = [f"{k}: {v.score}" for k, v in res.criteria.items()]
                    print(f"   -> Result: {res.decision} ({', '.join(scores)})")
                    save_screening_result(res.model_dump())
                    if is_fatal_error(res.notes):
                        print(f"   [FATAL] Screening cannot continue. Stopping.")
                        print(f"   {fatal_error_hint(res.notes)}")
                        break
                except Exception as e:
                    print(f"   [!] Error on {record.id}: {e}")
        finally:
            is_screening_running = False

    background_tasks.add_task(screen_task)
    return {"status": "started"}

@app.post("/api/screen/stop")
async def stop_screening():
    global stop_screening_flag
    stop_screening_flag = True
    return {"status": "stopped"}


@app.delete("/api/screen/results")
async def reset_screening_results():
    """Discard every screening result (e.g. after changing the criteria) so
    the whole corpus is re-screened on the next run. Records are kept."""
    if is_screening_running:
        return JSONResponse({"error": "Stop screening first."}, status_code=409)
    n = clear_screening_results()
    return {"status": "success", "deleted": n}

@app.get("/api/review")
async def get_reviews():
    results = get_all_screening_results()
    records = {r['id']: r for r in get_all_records()}
    
    to_review = []
    
    # "Maybe"s and "Exclude"s logic as in CLI
    for rid, r in results.items():
        if not r.get("human_reviewed"):
            # If decide to review all unreviewed maybes
            if r.get("decision") == "Maybe":
                to_review.append({"record": records[rid], "result": r})
    return {"reviews": to_review}

@app.post("/api/review/{record_id:path}")
async def submit_review(record_id: str, request: Request):
    data = await request.json()
    # update DB
    results = get_all_screening_results()
    if record_id in results:
        res = results[record_id]
        res['final_decision'] = data.get('decision')
        res['human_reviewed'] = True
        res['unmet_criteria'] = data.get('unmet_criteria', res.get('unmet_criteria', 'None'))
        save_screening_result(res)
        return {"status": "success"}
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/api/report")
async def download_report():
    # To use existing generate_report, we need an intermediate json file since the CLI function expects a JSON file path
    # Actually, we can rewrite the report logic or dump a temporary file
    results = get_all_screening_results()
    records = get_all_records()
    
    # Create dataset struct
    dataset = {
        "records": records,
        "screening_results": results
    }
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w") as tmp_in:
        import json
        json.dump(dataset, tmp_in)
        tmp_in_path = tmp_in.name
        
    out_csv = "data/screening_report_web.csv"
    os.makedirs("data", exist_ok=True)
    generate_report(tmp_in_path, out_csv)
    
    os.unlink(tmp_in_path)
    return FileResponse(out_csv, media_type="text/csv", filename="screening_report.csv")
