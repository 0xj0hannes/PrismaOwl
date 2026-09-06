"""Tests for src.harvest: provider parsing (HTTP mocked) and BibTeX output that
round-trips through src.ingestion."""
import json
import os

import pytest

import src.harvest as harvest
from src.ingestion import load_bibtex


class _Resp:
    def __init__(self, body=None, text=None, status=200):
        self._body = body
        self.text = text if text is not None else json.dumps(body)
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._body


def _mock_get(monkeypatch, responses):
    """``responses`` is a list consumed in order (one per HTTP call)."""
    it = iter(responses)
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {}), "headers": headers or {}})
        resp = next(it)
        return resp if isinstance(resp, _Resp) else _Resp(body=resp)

    monkeypatch.setattr(harvest.requests, "get", fake_get)
    monkeypatch.setattr(harvest.time, "sleep", lambda *_: None)
    return calls


def test_openalex_reconstructs_inverted_abstract_and_paginates(monkeypatch):
    page1 = {"meta": {"count": 2, "next_cursor": "c2"}, "results": [{
        "id": "https://openalex.org/W1", "title": "Profiling Hackers", "publication_year": 2020,
        "doi": "https://doi.org/10.1/abc",
        "abstract_inverted_index": {"We": [0], "study": [1], "hackers": [2]},
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "primary_location": {"source": {"display_name": "J. Crime", "type": "journal"}},
        "type": "article", "biblio": {"volume": "3", "first_page": "1", "last_page": "9"},
        "keywords": [{"display_name": "cybercrime"}],
    }]}
    page2 = {"meta": {"count": 2, "next_cursor": None}, "results": [{
        "id": "https://openalex.org/W2", "title": "Second", "publication_year": 2021,
        "authorships": [], "primary_location": {"source": {"type": "conference"}}, "type": "article",
    }]}
    calls = _mock_get(monkeypatch, [page1, page2])

    papers = list(harvest._openalex('hackers AND profiling', 10, {"OPENALEX_EMAIL": "me@x.org"}, None))

    assert len(papers) == 2
    assert papers[0].abstract == "We study hackers"
    assert papers[0].doi == "10.1/abc"
    assert papers[0].entry_type == "article"
    assert papers[0].pages == "1--9"
    assert papers[1].entry_type == "inproceedings"
    assert calls[0]["params"]["mailto"] == "me@x.org"
    assert calls[1]["params"]["cursor"] == "c2"


def test_max_results_is_respected(monkeypatch):
    results = [{"id": f"W{i}", "title": f"T{i}", "authorships": []} for i in range(5)]
    _mock_get(monkeypatch, [{"meta": {"count": 5, "next_cursor": "x"}, "results": results}])
    papers = list(harvest._openalex("q", 3, {}, None))
    assert [p.title for p in papers] == ["T0", "T1", "T2"]


def test_arxiv_parses_atom(monkeypatch):
    atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id>
    <title>A   Study
      of Hackers</title>
    <summary>Abstract text.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Ann Author</name></author>
    <author><name>Bob Writer</name></author>
    <arxiv:doi>10.5/xyz</arxiv:doi>
    <category term="cs.CR"/>
  </entry>
</feed>"""
    _mock_get(monkeypatch, [_Resp(text=atom)])
    papers = list(harvest._arxiv('all:hackers', 10, {}, None))
    assert len(papers) == 1
    p = papers[0]
    assert p.title == "A Study of Hackers"
    assert p.authors == ["Ann Author", "Bob Writer"]
    assert p.year == "2024" and p.doi == "10.5/xyz"
    assert p.source_id == "2401.00001v2"
    assert p.keywords == ["cs.CR"]


def test_crossref_strips_jats_and_formats_pages(monkeypatch):
    msg = {"message": {"total-results": 1, "next-cursor": None, "items": [{
        "DOI": "10.9/x", "title": ["Paper"], "abstract": "<jats:p>Hello &amp; bye</jats:p>",
        "author": [{"given": "A", "family": "B"}, {"name": "Consortium"}],
        "issued": {"date-parts": [[2019, 5]]}, "container-title": ["Journal"],
        "type": "journal-article", "page": "10-20"}]}}
    _mock_get(monkeypatch, [msg])
    p = list(harvest._crossref("paper", 10, {}, None))[0]
    assert p.abstract == "Hello & bye"
    assert p.authors == ["A B", "Consortium"]
    assert p.year == "2019" and p.pages == "10--20" and p.entry_type == "article"


def test_scopus_requires_key_and_ieee_requires_key():
    with pytest.raises(harvest.HarvestError):
        list(harvest._scopus("q", 1, {}, None))
    with pytest.raises(harvest.HarvestError):
        list(harvest._ieee("q", 1, {}, None))


def test_scopus_falls_back_to_standard_view(monkeypatch):
    entry = {"dc:title": "T", "dc:creator": "Doe J.", "prism:doi": "10.1/s", "prism:coverDate": "2022-01-01",
             "prism:publicationName": "Conf", "prism:aggregationType": "Conference Proceeding", "eid": "e1"}
    calls = _mock_get(monkeypatch, [
        _Resp(body={"error": "unauthorized"}, status=401),
        _Resp(body={"search-results": {"opensearch:totalResults": "1", "entry": [entry]}}),
    ])
    papers = list(harvest._scopus("TITLE-ABS-KEY(x)", 5, {"SCOPUS_API_KEY": "k"}, None))
    assert calls[0]["params"]["view"] == "COMPLETE"
    assert calls[1]["params"]["view"] == "STANDARD"
    assert papers[0].entry_type == "inproceedings"
    assert papers[0].authors == ["Doe J."]


def test_http_error_raises_harvest_error(monkeypatch):
    _mock_get(monkeypatch, [_Resp(body={"message": "bad"}, status=400)])
    with pytest.raises(harvest.HarvestError):
        list(harvest._openalex("q", 1, {}, None))


def test_harvest_rejects_manual_and_unknown_sources():
    with pytest.raises(harvest.HarvestError):
        harvest.harvest("acm", "q")
    with pytest.raises(harvest.HarvestError):
        harvest.harvest("nope", "q")
    with pytest.raises(harvest.HarvestError):
        harvest.harvest("openalex", "   ")


def test_bibtex_output_round_trips_through_ingestion(tmp_path):
    papers = [
        harvest.Paper(title="Deep & Wide: 50% of {AI} snake_case", abstract="A & B _ 100%",
                      authors=["Jane Doe", "Smith, John"], year="2020", doi="10.1/a",
                      venue="J. X", entry_type="article", volume="1", pages="1--2",
                      keywords=["k1", "k2"], source_db="openalex", source_id="W1"),
        harvest.Paper(title="Deep Learning Again", authors=["Jane Doe"], year="2020",
                      venue="Conf Y", entry_type="inproceedings", source_db="openalex", source_id="W2"),
        harvest.Paper(title="", authors=[], source_db="openalex"),   # skipped: no title
    ]
    bib = harvest.papers_to_bibtex(papers, query='"a" AND b', source="openalex")
    assert bib.startswith("% Generated by PrismaOwl")
    path = tmp_path / "out.bib"
    path.write_text(bib, encoding="utf-8")

    records = load_bibtex(str(path))
    assert len(records) == 2
    assert records[0].title == "Deep & Wide: 50% of AI snake_case"
    assert records[0].abstract == "A & B _ 100%"
    assert records[0].authors == "Doe, Jane, Smith, John"
    assert records[0].doi == "10.1/a"
    assert records[0].raw_data["journal"] == "J. X"
    assert records[1].raw_data["booktitle"] == "Conf Y"
    # keys are unique even for same first author + year + title word
    assert {r.id for r in records} == {"doe2020deep", "doe2020deepa"}


def test_harvest_to_file_writes_bib(monkeypatch, tmp_path):
    monkeypatch.setattr(harvest, "harvest",
                        lambda source, query, max_results=500, progress=None, years=None:
                        [harvest.Paper(title="X", abstract="y", year="2001", source_db=source)])
    out = tmp_path / "sub" / "x.bib"
    summary = harvest.harvest_to_file("openalex", "q", output=str(out), max_results=5)
    assert os.path.exists(out)
    assert summary["count"] == 1 and summary["with_abstract"] == 1


def test_list_sources_reports_key_availability(monkeypatch):
    monkeypatch.setattr(harvest, "load_config", lambda: {"SCOPUS_API_KEY": "k", "IEEE_API_KEY": ""})
    by_key = {s["key"]: s for s in harvest.list_sources()}
    assert by_key["openalex"]["available"] and not by_key["openalex"]["manual"]
    assert by_key["scopus"]["available"]
    assert not by_key["ieee"]["available"]
    assert by_key["acm"]["manual"] and by_key["web_of_science"]["manual"]


# ---------------------------------------------------------------------------
# Publication-year range applied per provider
# ---------------------------------------------------------------------------

def test_openalex_and_crossref_year_filters(monkeypatch):
    calls = _mock_get(monkeypatch, [{"meta": {"count": 0, "next_cursor": None}, "results": []},
                                    {"message": {"total-results": 0, "items": [], "next-cursor": None}}])
    list(harvest._openalex("q", 10, {}, None, (2010, 2024)))
    list(harvest._crossref("q", 10, {}, None, (2010, None)))
    assert calls[0]["params"]["filter"] == "from_publication_date:2010-01-01,to_publication_date:2024-12-31"
    assert calls[1]["params"]["filter"] == "from-pub-date:2010"


def test_semantic_scholar_and_ieee_year_params(monkeypatch):
    calls = _mock_get(monkeypatch, [{"total": 0, "data": []},
                                    {"total_records": 0, "articles": []}])
    list(harvest._semantic_scholar("q", 10, {}, None, (None, 2020)))
    list(harvest._ieee("q", 10, {"IEEE_API_KEY": "k"}, None, (2012, 2018)))
    assert calls[0]["params"]["year"] == "-2020"
    assert calls[1]["params"]["start_year"] == 2012 and calls[1]["params"]["end_year"] == 2018


def _arxiv_empty():
    return _Resp(text='<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
                      'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
                      '<opensearch:totalResults>0</opensearch:totalResults></feed>')


def test_scopus_and_arxiv_embed_years_in_query_unless_present(monkeypatch):
    calls = _mock_get(monkeypatch, [
        {"search-results": {"opensearch:totalResults": "0", "entry": []}},
        {"search-results": {"opensearch:totalResults": "0", "entry": []}},
        _arxiv_empty(),
    ])
    list(harvest._scopus("TITLE-ABS-KEY(x)", 10, {"SCOPUS_API_KEY": "k"}, None, (2010, 2024)))
    list(harvest._scopus("TITLE-ABS-KEY(x) AND PUBYEAR > 1999", 10, {"SCOPUS_API_KEY": "k"}, None, (2010, 2024)))
    list(harvest._arxiv("all:x", 10, {}, None, (2010, None)))
    assert calls[0]["params"]["query"] == "TITLE-ABS-KEY(x) AND PUBYEAR > 2009 AND PUBYEAR < 2025"
    assert calls[1]["params"]["query"] == "TITLE-ABS-KEY(x) AND PUBYEAR > 1999"     # user's own clause wins
    assert calls[2]["params"]["search_query"].startswith("(all:x) AND submittedDate:[20100101 TO ")


def test_normalize_years_orders_and_coerces():
    assert harvest._normalize_years(("2024", "2010")) == (2010, 2024)
    assert harvest._normalize_years((None, "")) == (None, None)
    assert harvest._normalize_years(None) == (None, None)
