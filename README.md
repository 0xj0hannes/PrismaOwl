# 🦉 PrismaOwl

**PrismaOwl** is an LLM-assisted workbench for **systematic literature reviews**. It takes a review from research question to PRISMA flow: the LLM drafts the **search strategy** (concept blocks and one query per database), proposes and refines the **inclusion criteria**, **screens** every title and abstract against those criteria with quoted evidence and a rationale per criterion, and sits beside the human reviewer as an **assistant** that weighs any uncertain record and answers questions about the corpus. Around it, the tool harvests records from the databases' APIs, deduplicates them, records every decision for the audit trail and draws the PRISMA 2020 flow [[1]](#references). Everything is configured and operated from a local web interface.

The LLM advises at every stage; the human decides. Screening decisions are reproducible by construction (one pinned model and one strictness level per review), every prompt and answer is logged, and every ambiguous case goes to a person.

It is **domain-agnostic**: the inclusion criteria are plain-language rules you write (or let the LLM draft) for your own review, so the same tool serves medicine, psychology, education, software engineering, criminology, or any other field. It does **not** replace the other PRISMA stages (protocol registration, full-text eligibility assessment, risk-of-bias appraisal, synthesis): those remain yours.

The LLM is reached either through [OrcaRouter](https://www.orcarouter.ai/ref/ref_92815be053059d75c9cb) (an OpenAI-compatible gateway to 100+ models, with a free routing mode) or directly through the Google Gemini API (free tier). No vendor SDK is involved.

> **Built with OrcaRouter.** PrismaOwl takes part in OrcaRouter's open-source program. Signing up through [this link](https://www.orcarouter.ai/ref/ref_92815be053059d75c9cb) supports the project's development at no cost to you; the tool works the same with any OrcaRouter account.

---

## 🎯 Statement of Need

The title/abstract screening phase of a systematic review is laborious and prone to reviewer fatigue: thousands of records must be judged against predefined inclusion criteria. Existing platforms such as [Rayyan](https://www.rayyan.ai/) and [ASReview](https://asreview.nl/) accelerate this with collaboration features and active-learning classifiers, but those classifiers require an accumulated set of human labels before they help, and they typically surface a single relevance ranking rather than an explicit, criterion-by-criterion justification.

This tool takes a complementary, zero-shot approach: it sends each record to an LLM together with your natural-language inclusion criteria and returns, **for every criterion**, a score, quoted supporting evidence, and a rationale — no training labels required. The model only triages; a human adjudicates every ambiguous (`Maybe`) case, and all prompts and raw responses are logged so each AI suggestion remains auditable. The same LLM helps before and after screening, where the manual work is just as real: turning a research question into database-specific Boolean queries, writing criteria that are precise enough to screen against, and weighing a borderline abstract with the whole corpus in view. It is built for researchers, librarians, and students who want to cut manual effort across the review without giving up the transparency and human oversight the methodology demands.

---

## ✨ Key Features

- 🖥 **One web interface for everything**: configuration (providers, keys, models, theme) and every stage of the review live in the browser; nothing to edit by hand.
- 🔎 **Search-strategy builder**: turn a research question into concept blocks and one ready-to-paste Boolean query per database (Scopus, Web of Science, IEEE Xplore, ACM DL, OpenAlex, Semantic Scholar, arXiv, Crossref). Rebuild the queries from edited concepts without an LLM call; a year range in the scope notes becomes a date filter everywhere.
- 🌐 **Harvesting into the corpus**: run a query against a database's official API (OpenAlex, Semantic Scholar, arXiv, Crossref without a key; Scopus and IEEE Xplore with a key). Hits are deduplicated and stored as a *harvest run* that remembers the query, database, date and year range, and can be exported as `.bib`.
- 📥 **Ingestion dashboard**: records identified, duplicates removed (with the reason and the record kept), per-source breakdown, drag-and-drop for BibTeX exports from databases without an API.
- 📋 **Criteria assistant and editor**: draft or refine inclusion criteria from the research question, edit every field; changes save automatically. Export the criteria and the search strategy as JSON for the protocol, and import a colleague's criteria to start from.
- 🔬 **Criterion-by-criterion screening**: every record gets a score, quoted evidence and a rationale per criterion plus an Include / Exclude / Maybe decision, at a **strictness** you choose (precision first, balanced, or recall first).
- 🔒 **Reproducible by construction**: screening is pinned to one named model and one strictness level per review; the model that actually judged each record is recorded, and the batch refuses to mix models or levels.
- 🤝 **Human-in-the-loop review with an assistant**: decide the `Maybe` cases with the corpus chatbot beside the cards; *Ask about this record* makes it weigh a paper against each criterion with quoted evidence and a recommendation. The decision stays yours.
- 📊 **PRISMA reporting**: a live PRISMA 2020 flow, counts per source, why records were excluded, the models and strictness used, a searchable table of every record, and a CSV export with one column triplet per criterion.
- 🆓 **Runs for free**: OrcaRouter's *Free* routing mode and Gemini's free tier both work without credits; a one-click *Auto / Free* toggle switches OrcaRouter between free and paid routing.
- 🧭 **Any discipline**: nothing about a research field is hard-coded.

---

## 🚀 Quick start

### 1. Prerequisites

- Python 3.9 or newer.
- An API key for **one** of the two LLM providers. You enter it in the app, not in a file.

**Option A — OrcaRouter (default).** [Sign up for OrcaRouter](https://www.orcarouter.ai/ref/ref_92815be053059d75c9cb) (referral link, see above), then create an API key (`sk-orca-…`) in the [console](https://www.orcarouter.ai/console). You do **not** have to add credits: OrcaRouter offers free upstream models, and the app's *Free* routing mode uses them. *Auto* routing (best model per request) needs credits; if you ever see `HTTP 402 insufficient_user_quota`, the account has no credits and the app is still on *Auto*.

**Option B — Google Gemini (free tier, no credit card).** Sign in to [Google AI Studio](https://aistudio.google.com/) and click **Get API key → Create API key**. As a new user, AI Studio provisions a default Google Cloud project for you and the key gets free-tier access immediately. If you already manage a GCP project you can instead enable the **Generative Language API** in [Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials) and create the key there (this is the free Gemini Developer API, not the billed Vertex AI).

> **Can't create a Gemini key?** If AI Studio refuses to generate one (it may look as if a paid plan were required), the usual cause is an unverified Google account age, not billing. Verify your age on your [Google Account](https://myaccount.google.com/) and retry — see [discussion #5](https://github.com/0xj0hannes/PrismaOwl/discussions/5). Keys created in AI Studio today are the newer *auth key* type; if you reuse an older *Standard* key, [migrate it](https://ai.google.dev/gemini-api/docs/api-key), as Google rejects Standard keys from September 2026.

### 2. Install and run

**macOS / Linux** — one command downloads PrismaOwl into a `PrismaOwl` folder, checks for Python 3.9+, creates a virtual environment and installs everything into that folder:

```bash
curl -fsSL https://raw.githubusercontent.com/0xj0hannes/PrismaOwl/main/install.sh | bash
cd PrismaOwl && ./start.sh
```

**Windows (PowerShell)** — download or clone the repository, then in its folder:

```powershell
.\install.ps1
.\start.ps1
```

`start.sh` / `start.ps1` start the server and open `http://127.0.0.1:8000` in your browser (`PORT=8080 ./start.sh` for another port; press Ctrl+C to stop). Run the installer again at any time to update the dependencies after a `git pull`.

<details>
<summary><strong>For developers: manual setup</strong></summary>

The scripts do nothing you cannot do by hand. From a clone:

```bash
git clone https://github.com/0xj0hannes/PrismaOwl.git
cd PrismaOwl

python3 -m venv .venv            # create the virtual environment (once)
source .venv/bin/activate        # macOS / Linux (bash, zsh)
# source .venv/bin/activate.fish # fish shell
# .venv\Scripts\activate         # Windows (PowerShell or cmd)

pip install -r requirements.txt
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in your browser. Activate the virtual environment in every new terminal before starting the server (fish users: the `activate.fish` script; the plain `activate` script is bash/zsh syntax). `--reload` restarts the server when Python files change; static files are versioned with a `?v=` query, so bump it and hard-refresh after editing them. Tests: `pip install -r requirements-dev.txt && pytest`.

</details>

### 3. Configure in the app

Click **⚙️ Settings** at the bottom of the sidebar (or open `http://127.0.0.1:8000/#settings`):

1. **LLM provider**: pick OrcaRouter or Google Gemini (or leave *auto-detect*), paste the API key, press **Test connection**. Keys are stored in the local `.env` file next to the app and are never shown again in the browser.
2. **Routing** (OrcaRouter only): choose **Free** to run without credits or **Auto** for the best paid model per request. *Free* also fills in a free screening model for you.
3. **Models**: **Load model list** pulls the ids your key can use into the model fields. The default model serves query building, criteria drafting and chat; the **screening** model must be one concrete model (see [Reproducibility](#-reproducibility-and-audit-trail)).
4. **Theme**: dark, light, or follow the system.

Press **Save**. Optional harvester credentials (an e-mail for OpenAlex/Crossref's polite pool, Semantic Scholar, Scopus, IEEE Xplore keys) are in the same dialog and only matter for those databases.

---

## 🧭 Workflow

The sidebar follows the review in order, and every tab ends with a **Next** button. `#<tab>` in the URL (for example `/#screen`) opens a tab directly.

1. **Search Strategy.** Enter your research question and press **Generate with AI** to get concept blocks (synonyms combined with OR, concepts combined with AND) and one query per database in that database's own syntax. Edit anything: once a strategy exists the button becomes **Refine with AI** and sends your concepts, queries and feedback to the model; **Start over from the research question** regenerates from scratch; **Rebuild from concepts** recomposes every query deterministically without an LLM call. A year range in the scope notes ("2010 onwards", "2015-2024") is written into the Scopus, Web of Science and arXiv queries and applied as an API filter when harvesting the others. Press **Run harvest** on any database with an open API: the hits are deduplicated into the corpus as a stored harvest run, downloadable as `.bib`. ACM DL and Web of Science have no open API; paste the query into their advanced search and upload the export on the next tab. Every edit saves automatically to the database; **Export JSON** downloads the strategy for your protocol or methods section. The **Advanced** section holds the LLM's rationale and the limitations of the search.

2. **Ingestion.** Records identified, unique records to screen, duplicates removed, per-source counts, and the list of harvest runs with what each contributed. Drag-and-drop `.bib` exports to add them. Duplicates (matched by DOI, or by normalised title + year + first author) are kept for the audit trail, listed with the record that was kept, and never screened. **Flush all ingested data** starts over with an empty corpus.

3. **Criteria.** Let the LLM draft inclusion criteria from your research question (or refine the current set with feedback) and edit every field: name, definition, signals to look for, negative indicators that must not count as evidence. Changes save automatically to the database; **Export JSON** downloads the set for your protocol and **Import JSON** replaces it with a file exported from another review (a sample set to try things out is in `examples/criteria.sample.json`). The criteria are the only place your research field enters the tool; they drive the screening prompt, the review buttons, the chat context and the CSV columns. Fix them before screening; changing them afterwards means resetting the results and screening again.

4. **Screening.** Choose the **decision strictness** — *Strict* (precision first, `Maybe` is rare), *Balanced* (every unclear abstract goes to review) or *Lenient* (recall first, only clearly off-topic papers are excluded) — and press **Start Screening**. Each unique record is sent to the pinned model with your criteria; the background job saves every result immediately, survives closed tabs, waits out rate limits, and stops on fatal errors with an explanation. **Reset screening results** clears the results to screen again from scratch.

5. **Review.** Every `Maybe` record appears as a card with the abstract and the per-criterion scores, evidence and rationale. Press **Include** or **Exclude** (with the unmet criteria). The assistant beside the cards has read the corpus: **Ask about this record** makes it assess the card criterion by criterion with quoted evidence and a recommendation, and you can ask it anything about the included papers. It advises; you decide, and the human decision is what the report records.

6. **Reports.** The PRISMA 2020 flow with live counts (records identified per source, duplicates removed, screened, excluded with the unmet criteria behind it, awaiting human decision, included; the full-text stages are shown greyed because they happen outside the tool), counts per source, why records were not included, the models and strictness used with mean criterion scores, and a searchable, filterable table of every record. **Download CSV report** exports one row per record with the decision, and for each criterion its score, evidence and rationale, plus the model version and strictness.

Every tab has a **?** button that explains the feature and the PRISMA 2020 checklist items it supports.

---

## 🔒 Reproducibility and audit trail

Screening decisions are what you report, so they must be reproducible and objective:

- **One model per review.** When the provider is OrcaRouter the screening model must be a concrete `vendor/model` id (the routing meta-models `orcarouter/auto`, `orcarouter/free` and `orcarouter/fusion-*` are refused for screening; a free concrete model such as `deepseek/deepseek-v4-flash-free` is fine). The model the router reports back is checked against the pin after every call, a substituted answer is discarded, and a batch will not start while existing results were judged by a different model.
- **One strictness level per review.** The level is recorded on every result and a batch refuses to mix levels.
- **Everything is recorded.** Each result stores the model that judged it and the strictness level (both in the CSV); all prompts and raw responses go to `logs/screening.log`; the duplicate records and harvest runs stay in the database so the PRISMA counts can be reproduced.

Meta-models remain fine for query building, criteria drafting and chat.

---

## 💾 Where your data lives

| Path | Contents |
| :--- | :--- |
| `data/prisma.db` | SQLite database holding the whole review: records (duplicates flagged), harvest runs, screening results and human decisions, plus the inclusion criteria and the search strategy. Back up this one file to keep a review. |
| `.env` | Provider keys, models and settings written by the Settings dialog. Never commit it. |
| `logs/screening.log` | Full prompts and raw LLM responses for the audit trail. |

---

## 🛠️ Command line (deprecated)

`main.py` still offers a file-based pipeline (`query`, `harvest`, `criteria`, `ingest`, `screen`, `review`, `report`, `chat`) that works on JSON and `.bib` files rather than the web corpus (it reads the criteria and strategy from the app's database unless you pass `--output`), and `test_llm.py` smoke-tests a provider from the terminal. **Both are deprecated and will be removed in a future release**: they receive no new features, they do not see the web corpus, and everything they do is available in the web interface (the Settings dialog replaces `test_llm.py`). Use `python3 main.py --help` if you still need them.

---

## 📂 Project Structure

| File | Description |
| :--- | :--- |
| `app.py` | FastAPI backend: REST API and the web interface. |
| `src/db.py` | SQLite corpus: records, harvest runs, screening results. |
| `src/llm.py` | OpenAI-compatible HTTP client for OrcaRouter and Gemini, provider selection, screening-model pin, error classification. |
| `src/prompts.py` | Screening prompt and the three strictness levels. |
| `src/screening.py` | Screening orchestration with retry semantics and the reproducibility guards. |
| `src/search_strategy.py` | LLM search-strategy builder, deterministic query builder, year-range parsing. |
| `src/harvest.py` | Database harvesters (OpenAlex, Semantic Scholar, arXiv, Crossref, Scopus, IEEE) and BibTeX import/export. |
| `src/deduplication.py` | DOI and title/year/author deduplication. |
| `src/criteria_assist.py` | LLM inclusion-criteria assistant and criteria validation. |
| `src/chat.py` | Assistant over the screened corpus, with the record-under-review focus. |
| `src/reporting.py` | CSV report. |
| `static/` | Vanilla HTML/CSS/JS frontend. |
| `install.sh`, `start.sh`, `install.ps1`, `start.ps1` | One-step install and start scripts (macOS/Linux and Windows). |
| `main.py`, `test_llm.py` | Deprecated command-line tools. |

---

## 📚 References

<a id="references"></a>

[1] Page M J, McKenzie J E, Bossuyt P M, Boutron I, Hoffmann T C, Mulrow C D et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews BMJ 2021; 372 :n71 doi:10.1136/bmj.n71

The PRISMA 2020 statement and flow diagram are made available by the PRISMA Group at [prisma-statement.org](https://www.prisma-statement.org/).

> **Disclaimer.** This software is an independent screening-assistant tool and is *not* endorsed by, or affiliated with, the PRISMA Group. It implements the identification and title/abstract screening stages of the PRISMA-recommended workflow; users remain responsible for adherence to the full reporting guideline.

---

## 🧪 Running the Tests

The test suite runs **offline** — no API key is required (the LLM client and all database HTTP calls are mocked).

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover ingestion and deduplication, the query builder and year-range parsing, the harvesters and BibTeX round-trip, the LLM client for both providers, the screening logic with its reproducibility guards, the strictness prompts, the chat assistant, and the web endpoints for settings, ingestion, harvest runs and reports. The same suite runs in CI on every push and pull request (see `.github/workflows/tests.yml`).

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and the pull-request workflow, and note our [Code of Conduct](CODE_OF_CONDUCT.md). Bug reports and feature requests go to the [issue tracker](https://github.com/0xj0hannes/PrismaOwl/issues).

---

## 📝 License
Released under the MIT License — see [LICENSE](LICENSE). Intended for academic research; please cite the PRISMA references above when reporting reviews conducted with this tool.
