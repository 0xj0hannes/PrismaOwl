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
    monkeypatch.setattr(web_app, "HARVEST_DIR", str(tmp_path / "harvest"))
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


def test_ingest_all_reads_every_harvested_file(web, tmp_path):
    hdir = tmp_path / "harvest"
    hdir.mkdir()
    (hdir / "openalex_1.bib").write_text(
        '@article{k1, title={Alpha Study}, author={Doe, Jane}, year={2020}, doi={10.1/alpha}}\n'
        '@article{k2, title={Beta Study}, author={Roe, Ann}, year={2021}}\n')
    (hdir / "arxiv_2.bib").write_text(
        '@article{k3, title={Alpha Study}, author={Doe, Jane}, year={2020}, doi={10.1/ALPHA}}\n')
    (hdir / "notes.txt").write_text("ignored")
    res = asyncio.run(web.harvest_ingest_all())
    assert res["uploaded"] == 3 and res["new_unique"] == 2 and res["new_duplicates"] == 1
    assert [f["file"] for f in res["files"]] == ["arxiv_2.bib", "openalex_1.bib"]
    assert sum(f["records"] for f in res["files"]) == 3

    (hdir / "openalex_1.bib").unlink(); (hdir / "arxiv_2.bib").unlink()
    assert asyncio.run(web.harvest_ingest_all()).status_code == 400


def _ingest(web, records):
    return web._ingest_records(records)
