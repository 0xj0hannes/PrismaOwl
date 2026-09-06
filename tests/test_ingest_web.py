"""Web ingestion path: duplicates persisted with reasons, dashboard stats,
and ingest-all over the harvest directory (temporary SQLite + temp dir)."""
import asyncio
import json
import os

import pytest

import src.db as db
from src.deduplication import split_duplicates
from tests.conftest import make_record


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import app as web_app
    return web_app


def test_split_duplicates_flags_reason_and_keeps_existing_canonical():
    canon = make_record(id="c", doi="10.1/x")
    dup_doi = make_record(id="d1", doi="10.1/X ")                      # same DOI
    dup_tya = make_record(id="d2", doi=None)                           # same title/year/author
    # A previously stored duplicate listed first must not steal the canonical role.
    stale = make_record(id="d0", doi="10.1/x", is_duplicate=True, duplicate_of="c")
    canonical, duplicates = split_duplicates([stale, canon, dup_doi, dup_tya])
    assert [r.id for r in canonical] == ["c"]
    assert sorted(r.id for r in duplicates) == ["d0", "d1", "d2"]
    assert all(r.duplicate_of == "c" for r in duplicates)
    assert dup_doi.duplicate_reason.startswith("DOI match")
    assert dup_tya.duplicate_reason == "Title + Year + Author match"


def test_ingest_records_stores_duplicates_and_stats(web):
    r1 = _ingest(web, [make_record(id="a", doi="10.1/a", source_file="s1.bib"),
                       make_record(id="b", title="Other Study", doi="10.1/b", source_file="s1.bib")])
    assert (r1["new_unique"], r1["new_duplicates"], r1["total_unique_db"]) == (2, 0, 2)

    r2 = _ingest(web, [make_record(id="a2", doi="10.1/A", source_file="s2.bib"),        # dup of a by DOI
                       make_record(id="c", title="Third", doi=None, source_file="s2.bib")])
    assert (r2["uploaded"], r2["new_unique"], r2["new_duplicates"]) == (2, 1, 1)
    assert r2["total_unique_db"] == 3 and r2["total_duplicates_db"] == 1

    # Screening only sees canonical records; the dashboard sees everything.
    assert sorted(r["id"] for r in db.get_unique_records()) == ["a", "b", "c"]
    stats = asyncio.run(web.ingest_stats())
    assert (stats["total_records"], stats["unique"], stats["duplicates"]) == (4, 3, 1)
    assert stats["sources"] == [{"source_file": "s1.bib", "records": 2, "duplicates": 0},
                                {"source_file": "s2.bib", "records": 2, "duplicates": 1}]
    (dup,) = stats["duplicate_list"]
    assert dup["id"] == "a2" and dup["duplicate_of"] == "a"
    assert dup["reason"].startswith("DOI match") and dup["canonical_source"] == "s1.bib"
    assert stats["duplicate_list_truncated"] is False

    # Re-ingesting the same file again changes nothing: 'a' stays canonical.
    r3 = _ingest(web, [make_record(id="a3", doi="10.1/a", source_file="s3.bib")])
    assert r3["new_duplicates"] == 1 and r3["total_unique_db"] == 3
    assert db.get_all_records() and all(r["id"] != "a" or not r["is_duplicate"] for r in db.get_all_records())


def _ingest(web, records):
    return web._ingest_records(records)


def test_reingesting_same_file_is_skipped_and_key_clash_gets_new_id(web):
    _ingest(web, [make_record(id="smith2020", doi="10.1/a", source_file="f1.bib")])
    # Same key, same file: already ingested, nothing changes, canonical row untouched.
    r = _ingest(web, [make_record(id="smith2020", doi="10.1/a", source_file="f1.bib")])
    assert r["already_ingested"] == 1 and r["new_unique"] == 0 and r["new_duplicates"] == 0
    rows = {x["id"]: x for x in db.get_all_records()}
    assert rows["smith2020"]["is_duplicate"] is False and len(rows) == 1

    # Same key from another file but a different paper: renamed, stays unique.
    r = _ingest(web, [make_record(id="smith2020", title="A Different Paper", doi="10.1/z", source_file="f2.bib")])
    assert r["new_unique"] == 1
    rows = {x["id"]: x for x in db.get_all_records()}
    assert set(rows) == {"smith2020", "smith2020_2"}
    assert rows["smith2020"]["is_duplicate"] is False and rows["smith2020_2"]["is_duplicate"] is False

    # Same key from another file and the same paper: renamed and flagged duplicate of the original.
    r = _ingest(web, [make_record(id="smith2020", doi="10.1/a", source_file="f3.bib")])
    assert r["new_duplicates"] == 1
    rows = {x["id"]: x for x in db.get_all_records()}
    assert rows["smith2020_3"]["is_duplicate"] is True and rows["smith2020_3"]["duplicate_of"] == "smith2020"
    assert rows["smith2020"]["is_duplicate"] is False


# ---------------------------------------------------------------------------
# Harvest runs stored in SQLite
# ---------------------------------------------------------------------------

def _papers():
    from src.harvest import Paper
    return [Paper(title="Alpha Study", abstract="A.", authors=["Jane Doe", "John Roe"], year="2020",
                  doi="10.1/alpha", venue="J. Test", entry_type="article", source_db="openalex", source_id="W1"),
            Paper(title="Beta Study", authors=["Ann Poe"], year="2021", source_db="openalex", source_id="W2")]


def test_papers_to_records_and_bibtex_round_trip():
    from src.harvest import papers_to_records, records_to_bibtex
    from src.ingestion import load_bibtex
    recs = papers_to_records(_papers(), "OpenAlex harvest 2026-09-06 16:45", harvest_id="h1")
    assert [r.id for r in recs] == ["doe2020alpha", "poe2021beta"]
    assert recs[0].authors == "Doe, Jane, Roe, John" and recs[0].doi == "10.1/alpha"
    assert recs[0].harvest_id == "h1" and recs[0].normalized_title
    assert recs[0].raw_data["source_id"] == "W1"
    bib = records_to_bibtex([r.model_dump() for r in recs], query="q", source="openalex")
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".bib", delete=False) as f:
        f.write(bib)
    try:
        loaded = load_bibtex(f.name)
    finally:
        os.unlink(f.name)
    assert [r.title for r in loaded] == ["Alpha Study", "Beta Study"]
    assert loaded[0].doi == "10.1/alpha"


def test_harvest_job_stores_records_and_run(web, monkeypatch):
    monkeypatch.setattr(web, "harvest", lambda source, query, max_results, progress, years=(None, None): _papers())
    job = {"id": "h1", "source": "openalex", "query": "alpha", "max_results": 10, "years": [2015, 2024],
           "years_label": "2015\u20132024", "status": "running", "fetched": 0, "total": None,
           "error": None, "result": None, "started": "2026-09-06T16:45:00"}
    web.harvest_jobs["h1"] = job
    web._run_harvest_job(job)

    assert job["status"] == "done" and "h1" not in web.harvest_jobs
    assert job["result"]["count"] == 2 and job["result"]["new_unique"] == 2
    assert job["label"] == "OpenAlex harvest 2026-09-06 16:45"
    runs = asyncio.run(web.harvest_runs())["runs"]
    assert [r["id"] for r in runs] == ["h1"] and runs[0]["status"] == "done"
    stored = db.get_records_for_harvest("h1")
    assert sorted(r["id"] for r in stored) == ["doe2020alpha", "poe2021beta"]
    assert all(r["source_file"] == "OpenAlex harvest 2026-09-06 16:45" for r in stored)

    # A second run of the same query: everything is a duplicate of run 1.
    job2 = dict(job, id="h2", started="2026-09-06T17:00:00", status="running", result=None)
    web.harvest_jobs["h2"] = job2
    web._run_harvest_job(job2)
    assert job2["result"]["new_unique"] == 0 and job2["result"]["new_duplicates"] == 2
    assert asyncio.run(web.ingest_stats())["duplicates"] == 2

    # Download regenerates BibTeX from the stored records.
    from src.harvest import records_to_bibtex
    assert "Alpha Study" in records_to_bibtex(db.get_records_for_harvest("h1"))
    resp = asyncio.run(web.harvest_run_download("h1"))
    assert resp.status_code == 200 and resp.media_type == "application/x-bibtex"
    assert 'filename="openalex_20260906_164500.bib"' in resp.headers["content-disposition"]
    assert asyncio.run(web.harvest_run_download("nope")).status_code == 404

    # Deleting run 2 removes its (unscreened) records; a screened record would be kept.
    db.save_screening_result({"record_id": "doe2020alpha_2", "decision": "Include"})
    res = asyncio.run(web.harvest_run_delete("h2"))
    assert res["records_removed"] == 1 and res["records_kept"] == 1
    assert asyncio.run(web.harvest_runs())["runs"][0]["id"] == "h1"
    assert asyncio.run(web.harvest_run_delete("nope")).status_code == 404


def test_harvest_job_failure_is_recorded(web, monkeypatch):
    def boom(*a, **k):
        raise web.HarvestError("SCOPUS_API_KEY is not set in .env")
    monkeypatch.setattr(web, "harvest", boom)
    job = {"id": "hx", "source": "scopus", "query": "q", "max_results": 5, "years": [None, None],
           "years_label": "", "status": "running", "fetched": 0, "total": None, "error": None,
           "result": None, "started": "2026-09-06T18:00:00"}
    web._run_harvest_job(job)
    run = asyncio.run(web.harvest_runs())["runs"][0]
    assert run["status"] == "failed" and "SCOPUS_API_KEY" in run["error"]
    assert db.get_records_for_harvest("hx") == []


def test_duplicates_endpoint_pages(web):
    canon = make_record(id="c", doi="10.1/c", source_file="f.bib")
    dups = [make_record(id=f"d{i}", doi="10.1/c", source_file="g.bib") for i in range(7)]
    _ingest(web, [canon] + dups)
    p1 = asyncio.run(web.ingest_duplicates(offset=0, limit=3))
    p2 = asyncio.run(web.ingest_duplicates(offset=3, limit=3))
    p3 = asyncio.run(web.ingest_duplicates(offset=6, limit=3))
    assert (p1["total"], len(p1["items"]), p1["has_more"]) == (7, 3, True)
    assert (len(p2["items"]), p2["has_more"]) == (3, True)
    assert (len(p3["items"]), p3["has_more"]) == (1, False)
    ids = [r["id"] for r in p1["items"] + p2["items"] + p3["items"]]
    assert len(set(ids)) == 7 and all(r["duplicate_of"] == "c" for r in p1["items"])
    # The stats endpoint carries the first page and the has-more flag.
    st = asyncio.run(web.ingest_stats(limit=5))
    assert len(st["duplicate_list"]) == 5 and st["duplicate_list_truncated"] is True


def test_flush_removes_records_runs_and_results(web, monkeypatch):
    _ingest(web, [make_record(id="a", doi="10.1/a"), make_record(id="a2", doi="10.1/a", source_file="x")])
    db.save_harvest({"id": "h1", "source": "openalex", "status": "done", "started": "2026-09-06T10:00:00"})
    db.save_screening_result({"record_id": "a", "decision": "Include"})
    monkeypatch.setattr(web, "is_screening_running", True)
    assert asyncio.run(web.delete_all_ingested()).status_code == 409
    monkeypatch.setattr(web, "is_screening_running", False)
    res = asyncio.run(web.delete_all_ingested())
    assert (res["records"], res["harvests"], res["screening_results"]) == (2, 1, 1)
    assert db.get_all_records() == [] and db.get_harvests() == [] and db.get_all_screening_results() == {}
    assert asyncio.run(web.ingest_stats())["total_records"] == 0


# ---------------------------------------------------------------------------
# Reports dashboard: summary counts and paged results
# ---------------------------------------------------------------------------

def test_report_summary_and_results(web, monkeypatch):
    _ingest(web, [make_record(id="a", title="Alpha", doi="10.1/a", source_file="s1"),
                  make_record(id="b", title="Beta", doi="10.1/b", source_file="s1"),
                  make_record(id="c", title="Gamma", doi="10.1/c", source_file="s2"),
                  make_record(id="d", title="Delta", doi="10.1/d", source_file="s2"),
                  make_record(id="a2", title="Alpha", doi="10.1/A", source_file="s2")])   # duplicate of a
    db.save_screening_result({"record_id": "a", "decision": "Include", "model_version": "m1", "strictness": "strict",
                              "criteria": {"IC1": {"score": 0.9}, "IC2": {"score": 0.8}}})
    db.save_screening_result({"record_id": "b", "decision": "Maybe", "unmet_criteria": "IC2", "model_version": "m1",
                              "criteria": {"IC1": {"score": 0.7}, "IC2": {"score": 0.3}}})
    db.save_screening_result({"record_id": "c", "decision": "Maybe", "final_decision": "Exclude",
                              "human_reviewed": True, "unmet_criteria": "IC2", "model_version": "m1"})
    db.save_screening_result({"record_id": "d", "decision": "Maybe", "notes": "Failed after 3 attempts. Last error: x"})
    monkeypatch.setattr(web, "load_config", lambda: {"CRITERIA": {"IC1": {"name": "One"}, "IC2": {"name": "Two"}}})

    s = asyncio.run(web.report_summary())
    c = s["counts"]
    assert (c["identified"], c["duplicates_removed"], c["unique"]) == (5, 1, 4)
    assert (c["included"], c["excluded"], c["maybe"], c["failed"], c["not_screened"]) == (1, 1, 1, 1, 0)
    assert c["screened"] == 3 and c["human_reviewed"] == 1
    assert s["unmet_criteria"] == [{"criteria": "IC2", "count": 2}]
    assert s["models"] == {"m1": 3} and s["strictness"] == {"strict": 3}
    assert s["avg_scores"] == {"IC1": 0.8, "IC2": 0.55}
    src = {x["source_file"]: x for x in s["sources"]}
    assert src["s2"]["identified"] == 3 and src["s2"]["duplicates"] == 1 and src["s2"]["excluded"] == 1

    r = asyncio.run(web.report_results(limit=2))
    assert r["total"] == 4 and [x["id"] for x in r["items"]] == ["a", "b"]     # Include first, then Maybe
    assert r["items"][0]["scores"] == {"IC1": 0.9, "IC2": 0.8} and r["criteria"] == ["IC1", "IC2"]
    r2 = asyncio.run(web.report_results(offset=2, limit=2))
    assert [x["id"] for x in r2["items"]] == ["c", "d"] and r2["has_more"] is False
    assert r2["items"][0]["decision"] == "Exclude" and r2["items"][0]["human_reviewed"] is True
    assert r2["items"][1]["decision"] == "Failed"
    assert [x["id"] for x in asyncio.run(web.report_results(decision="maybe"))["items"]] == ["b"]
    assert [x["id"] for x in asyncio.run(web.report_results(q="gam"))["items"]] == ["c"]


def test_chat_endpoint_passes_focus_and_validates(web, monkeypatch):
    class _Req:
        def __init__(self, payload): self._p = payload
        async def json(self): return self._p
    captured = {}
    monkeypatch.setattr(web, "chat_ask", lambda messages, records, results, criteria, scope, focus=None:
                        captured.update(scope=scope, focus=focus) or {"reply": "ok", "n_records": 0, "scope": scope})
    res = asyncio.run(web.chat_endpoint(_Req({"messages": [{"role": "user", "content": "hi"}],
                                             "scope": "included_maybe", "focus_record_id": " r1 "})))
    assert res["reply"] == "ok" and captured == {"scope": "included_maybe", "focus": "r1"}
    assert asyncio.run(web.chat_endpoint(_Req({"messages": [], "scope": "included"}))).status_code == 400
    assert asyncio.run(web.chat_endpoint(_Req({"messages": [{"role": "user", "content": "x"}], "scope": "bogus"}))).status_code == 400
    assert set(asyncio.run(web.chat_scope_counts())["scopes"]) == {"included", "included_maybe", "all_screened"}


# ---------------------------------------------------------------------------
# Criteria + search strategy stored in the database
# ---------------------------------------------------------------------------

def test_documents_round_trip_and_legacy_import(tmp_path, monkeypatch):
    import src.config as cfgmod
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "docs.db"))
    crit_file = tmp_path / "criteria.json"
    strat_file = tmp_path / "search_strategy.json"
    crit_file.write_text('{"IC1": {"name": "n", "definition": "d", "signals": "", "negative_indicators": ""}}')
    strat_file.write_text('{"research_question": "rq", "concepts": []}')
    monkeypatch.setattr(cfgmod, "CRITERIA_PATH", str(crit_file))
    monkeypatch.setattr(cfgmod, "SEARCH_STRATEGY_PATH", str(strat_file))

    db.init_db()                                   # imports both files once
    assert cfgmod.load_criteria() == {"IC1": {"name": "n", "definition": "d", "signals": "", "negative_indicators": ""}}
    assert cfgmod.load_search_strategy()["research_question"] == "rq"

    cfgmod.save_criteria({"IC9": {"name": "x", "definition": "y"}})
    crit_file.write_text('{"IC2": {"name": "changed on disk"}}')
    db.init_db()                                   # a second start must not re-import over DB content
    assert list(cfgmod.load_criteria()) == ["IC9"]
    assert cfgmod.load_config()["CRITERIA"] == {"IC9": {"name": "x", "definition": "y"}}
    # explicit paths still read/write files (exports, deprecated CLI)
    cfgmod.save_criteria({"IC3": {"name": "f", "definition": "g"}}, path=str(tmp_path / "out.json"))
    assert cfgmod.load_criteria(str(tmp_path / "out.json")) == {"IC3": {"name": "f", "definition": "g"}}


def test_criteria_export_and_import_endpoints(web, monkeypatch):
    import src.config as cfgmod
    cfgmod.save_criteria({"IC1": {"name": "One", "definition": "D1", "signals": "", "negative_indicators": ""}})
    monkeypatch.setattr(web.screening_module, "reload_config", lambda: None)
    exported = asyncio.run(web.export_criteria())
    assert exported.headers["content-disposition"].endswith('filename="criteria.json"')
    assert json.loads(exported.body) == cfgmod.load_criteria()

    class _Upload:
        def __init__(self, data): self._d = data
        async def read(self): return self._d
    res = asyncio.run(web.import_criteria(_Upload(b'{"IC2": {"name": "Two", "definition": "D2"}}')))
    assert list(res["criteria"]) == ["IC2"] and list(cfgmod.load_criteria()) == ["IC2"]
    assert asyncio.run(web.import_criteria(_Upload(b'not json'))).status_code == 400
    assert asyncio.run(web.import_criteria(_Upload(b'{"bad key!": {"name": "x", "definition": "y"}}'))).status_code == 400
    monkeypatch.setattr(web, "is_screening_running", True)
    assert asyncio.run(web.import_criteria(_Upload(b'{}'))).status_code == 409

    cfgmod.save_search_strategy({"research_question": "rq", "concepts": [], "queries": {}})
    out = asyncio.run(web.export_search_strategy())
    assert json.loads(out.body)["research_question"] == "rq"
