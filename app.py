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
from src.screening import screen_record, is_model_unavailable, MODEL_UNAVAILABLE_HINT
from src.reporting import generate_report
from src.db import init_db, save_record, save_screening_result, get_all_records, get_all_screening_results, get_unique_records
from src.config import load_config

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
    return {"screened": len(all_res), "total": total, "is_running": is_screening_running}

@app.post("/api/screen/start")
async def start_screening(background_tasks: BackgroundTasks):
    global stop_screening_flag, is_screening_running
    stop_screening_flag = False
    
    if is_screening_running:
        return {"status": "already started"}
        
    is_screening_running = True
    
    # Retrieve from DB
    records = get_unique_records()
    already_screened = get_all_screening_results()
    
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
                    if is_model_unavailable(res.notes):
                        print(f"   [FATAL] Model unavailable. Stopping screening.")
                        print(f"   {MODEL_UNAVAILABLE_HINT}")
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
