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
