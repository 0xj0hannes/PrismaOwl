"""Text files must be opened as UTF-8 regardless of the platform default
(issue #16: Windows' CP1252 default raised UnicodeDecodeError on the web shell).
Simulated by making every ``open()`` without an explicit encoding use CP1252."""
import asyncio
import builtins
import functools
import json

import pytest

from src.config import update_env
from src.reporting import generate_report
from src.review import load_screening_results, save_results

NON_ASCII = "Ünïcödé — 🦉 – naïve résumé"


@pytest.fixture
def cp1252_default(monkeypatch):
    real_open = builtins.open

    @functools.wraps(real_open)
    def windows_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and "encoding" not in kwargs and len(args) < 3:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", windows_open)
    # Sanity check: the simulated default really does choke on the shell.
    with pytest.raises(UnicodeDecodeError):
        with real_open("static/index.html", encoding="cp1252") as f:
            f.read()


def test_web_shell_is_served_under_cp1252_default(cp1252_default, tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import app as web_app
    response = asyncio.run(web_app.read_root())
    assert response.status_code == 200
    assert "🦉" in response.body.decode("utf-8")


def test_env_file_round_trips_non_ascii(cp1252_default, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"# {NON_ASCII}\nMODEL_NAME=x\n", encoding="utf-8")
    update_env({"MODEL_NAME": NON_ASCII}, path=str(env))
    text = env.read_text(encoding="utf-8")
    assert f"# {NON_ASCII}" in text
    assert NON_ASCII in text.splitlines()[1]


def test_review_results_round_trip_non_ascii(cp1252_default, tmp_path):
    path = tmp_path / "r.json"
    data = {"records": [{"id": "1", "title": NON_ASCII}], "screening_results": {}}
    save_results(data, str(path))
    assert load_screening_results(str(path)) == data


def test_report_reads_utf8_input(cp1252_default, tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"records": [{"id": "1", "title": NON_ASCII, "abstract": ""}],
                               "screening_results": {}}, ensure_ascii=False), encoding="utf-8")
    generate_report(str(src), str(tmp_path / "out.csv"))
    # A CP1252 read would not raise here but would garble the title (mojibake).
    assert NON_ASCII in (tmp_path / "out.csv").read_text(encoding="utf-8")
