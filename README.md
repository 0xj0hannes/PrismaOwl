# 🦉 PrismaOwl

**PrismaOwl** is a high-precision, LLM-assisted screening assistant designed for **Systematic Literature Reviews**. This system leverages LLMs, either routed through [OrcaRouter](https://www.orcarouter.ai) (an OpenAI-compatible gateway to 100+ models) or called directly on the Google Gemini API, to accelerate the search-strategy design, literature harvesting and title/abstract screening phases of the PRISMA 2020 reporting guideline [[1]](#references). It is **domain-agnostic**: the inclusion criteria are plain-language rules you write (or let the LLM draft) for your own review, so the same tool serves medicine, psychology, education, software engineering, criminology, or any other field.

The tool supports the *identification* (search strategy + database harvesting) and *screening* stages described in PRISMA 2020 [[1]](#references), plus an LLM chat over the included records to help with synthesis; it does **not** replace the other PRISMA stages (protocol registration, eligibility assessment of full texts, risk-of-bias appraisal, or reporting). All AI-suggested decisions are user-configurable inclusion criteria, fully logged for audit, and surfaced for human adjudication in the "Maybe" review queue.

---

## 🎯 Statement of Need

The title/abstract screening phase of a systematic review is laborious and prone to reviewer fatigue: thousands of records must be judged against predefined inclusion criteria. Existing platforms such as [Rayyan](https://www.rayyan.ai/) and [ASReview](https://asreview.nl/) accelerate this with collaboration features and active-learning classifiers, but those classifiers require an accumulated set of human labels before they help, and they typically surface a single relevance ranking rather than an explicit, criterion-by-criterion justification.

This tool takes a complementary, zero-shot approach: it sends each record to an LLM together with your natural-language inclusion criteria and returns, **for every criterion**, a score, quoted supporting evidence, and a rationale — no training labels required. The model only triages; a human adjudicates every ambiguous (`Maybe`) case, and all prompts and raw responses are logged so each AI suggestion remains auditable. It is built for researchers, librarians, and students who want to cut manual screening effort without giving up the transparency and human oversight the methodology demands.

---

## ✨ Key Features

- 🔎 **LLM Search-Strategy Builder**: Turn a research question into concept blocks and ready-to-paste boolean queries for Scopus, Web of Science, IEEE Xplore, ACM DL, OpenAlex, Semantic Scholar, arXiv and Crossref — then edit them in the web UI.
- 🌐 **Automated Harvesting**: Run a query against a database's official API (OpenAlex, Semantic Scholar, arXiv, Crossref without any key; Scopus and IEEE Xplore with a key). Hits go straight into the deduplicated corpus as a stored harvest run (query, database, date, year range), and every run can be exported as a clean `.bib` file.
- 📋 **LLM Criteria Assistant + Editor**: Draft or refine inclusion criteria from the research question, edit every field in the browser, and save straight to `criteria.json`.
- 💬 **Ask-the-corpus Chatbot**: Chat with an LLM that has read every included record; answers cite record IDs.
- 🔀 **Model Routing via OrcaRouter, or Gemini directly**: One OrcaRouter key gives you any model (`orcarouter/auto` for adaptive routing, or pin a different model per task: screening, query building, criteria, chat). Prefer Google's free tier? Set `GEMINI_API_KEY` and the same code talks to the Gemini API instead.
- 🆓 **Runs for free**: OrcaRouter has a **Free** routing mode (`orcarouter/free` plus free `-free` models such as `deepseek/deepseek-v4-flash-free` for screening) that needs no credits, and Gemini's free tier needs no credit card. A one-click *Auto / Free* toggle in Settings switches OrcaRouter between the two.
- 🖥 **Premium Web Dashboard**: A sleek, dark-mode GUI natively powered by FastAPI for seamless visual ingestion, screening, and human-in-the-loop review.
- 🔬 **High-Precision AI Screening**: Engineered to favour precision over recall—every criterion needs explicit, quoted evidence from the title or abstract before a record is included.
- 🧭 **Any discipline**: Nothing about a research field is hard-coded. Criteria, search concepts and the chat all derive from your own research question.
- 💾 **Hybrid Persistence**: Robust JSON databasing for CLI scripts and heavy-duty global deduplication SQLite persistence (`data/prisma.db`) for web operations.
- 🤝 **Interactive Human-in-the-loop**: Rapid-fire visual interface for the manual review of ambiguous AI decisions ("Maybe" cases).
- 🔄 **Stateful Execution**: Automatic background save-states guarantee you will never lose screening work from API limits or closed tabs.
- 📂 **Bulk BibTeX Uploads**: Visually drag-and-drop massive batches of `.bib` records directly into the unified screening queue with instant duplicate rejection.

---

## 🚀 Setup & Configuration

### 1. Prerequisites
- Python 3.9+
- An API key for **one** of the two supported LLM providers:

**Option A — OrcaRouter (default):** an [OrcaRouter](https://www.orcarouter.ai) API key (`sk-orca-…`) with some credit on the account. OrcaRouter is an OpenAI-compatible gateway that routes each request to one of 100+ upstream models (Anthropic, OpenAI, Google, DeepSeek, Qwen, …), so you never have to manage per-vendor keys or chase deprecated model names. Sign in to the [OrcaRouter console](https://www.orcarouter.ai/console) and create an API key.

> **Free mode (no credits needed).** You do not have to add credits. OrcaRouter offers free upstream models: the `orcarouter/free` meta-model routes to whichever free model is available, and concrete free models are listed with a `-free` suffix (for example `deepseek/deepseek-v4-flash-free`, `qwen/qwen3.8-27b-free`, `tencent/hy3-free`; run `python3 test_llm.py --models` to see the current list). Choose **Free** in the *Auto / Free* routing toggle of the ⚙️ Settings dialog, or set `MODEL_NAME=orcarouter/free` and `MODEL_SCREENING=<a -free model>` in `.env`. Free models are slower and rate-limited; **Auto** (`orcarouter/auto`, best model per request) needs credits. If you see `HTTP 402 insufficient_user_quota`, the account has no credits and you are still on *Auto*.

**Option B — Google Gemini (free tier, no credit card):** a [Google AI Studio](https://aistudio.google.com/) API key. Sign in with a Google account and click **Get API key** → **Create API key**. As a new user, AI Studio automatically provisions a default Google Cloud project for you, and the key gets free-tier access immediately — no billing account needed. If you already manage a GCP project you can instead enable the **Generative Language API** in [Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials) and create the key there (this is still the free Gemini Developer API, not the billed Vertex AI). PrismaOwl talks to Gemini through its OpenAI-compatible endpoint, so no Google SDK is installed.

> **Can't create a Gemini key?** If Google AI Studio refuses to generate an API key (e.g. it appears to require a paid plan), this is usually caused by an unverified Google account age, not billing. Verify your age on your [Google Account](https://myaccount.google.com/) (a government ID may be requested) and retry — see [discussion #5](https://github.com/0xj0hannes/PrismaOwl/discussions/5).

> **Note on Gemini key types:** Keys created in AI Studio today are issued as the newer *auth key* type, so a fresh key already meets current requirements. If you are reusing an older *Standard* key, migrate it — [Google's docs](https://ai.google.dev/gemini-api/docs/api-key) state the Gemini API will reject Standard keys from September 2026, and unrestricted keys left dormant for an extended period are blocked (shown with a **Blocked** tag in AI Studio). If screening suddenly fails on a key that used to work, check this first.

### 2. Installation
Clone the repository and install the dependencies into a virtual environment so they stay isolated from your system Python:

```bash
git clone https://github.com/0xj0hannes/PrismaOwl.git
cd PrismaOwl

python3 -m venv .venv            # create the virtual environment (once)
source .venv/bin/activate        # macOS / Linux (bash, zsh)
# source .venv/bin/activate.fish # fish shell
# .venv\Scripts\activate         # Windows (PowerShell or cmd)

pip install -r requirements.txt
```

Activate the environment in every new terminal before running the commands below (use the `activate.fish` script if your shell is fish; the plain `activate` script is bash/zsh syntax and fails there). If you prefer not to activate it, prefix commands with `.venv/bin/python` instead of `python3`.

### 3. Environment Setup
Create a `.env` file in the root directory (or enter everything in the ⚙️ Settings dialog of the web UI, which writes the same file). With OrcaRouter and credits on the account:
```env
ORCA_API_KEY=sk-orca-your_key_here
MODEL_NAME=orcarouter/auto      # or any id from `python3 test_llm.py`, e.g. anthropic/claude-sonnet-5
MODEL_SCREENING=anthropic/claude-sonnet-5   # screening needs one concrete model
MAX_RETRIES=3
```

With OrcaRouter and **no credits** (free mode):
```env
ORCA_API_KEY=sk-orca-your_key_here
MODEL_NAME=orcarouter/free
MODEL_SCREENING=deepseek/deepseek-v4-flash-free
MAX_RETRIES=3
```

Or with Gemini:
```env
GEMINI_API_KEY=your_api_key_here
MODEL_NAME=gemini-3.5-flash
MAX_RETRIES=3
```

The provider is picked from whichever key is present (OrcaRouter wins if both are set); set `LLM_PROVIDER=orcarouter` or `LLM_PROVIDER=gemini` to choose explicitly. Google deprecates older Gemini models over time: if screening fails with a `404` such as *"This model is no longer available to new users"*, run `python3 test_llm.py` to list the models available to your key and set `MODEL_NAME` accordingly.

Per-task overrides let you use one model for screening and others for the search-strategy builder or chatbot:

```env
MODEL_SCREENING=anthropic/claude-sonnet-5
MODEL_QUERY=orcarouter/auto
MODEL_CRITERIA=
MODEL_CHAT=google/gemini-3.5-flash
```

> **Screening is pinned to one model.** Screening decisions are what you report, so they must be reproducible and objective: every record of a review has to be judged by the same, explicitly named model. When the provider is OrcaRouter, `MODEL_SCREENING` (or `MODEL_NAME` if the override is empty) must therefore be a concrete `vendor/model` id. The routing meta-models `orcarouter/auto`, `orcarouter/free` and `orcarouter/fusion-*` are refused for screening, the model id the router reports back is checked against the pin after every call (a substituted answer is discarded and the batch stops), and a batch will not start while existing results were produced by a different model — restore the previous model or reset the screening results first. Each result stores the model that judged it (`model_version`) for the audit trail. Meta-models remain fine for query building, criteria drafting and chat.

Run `python3 test_llm.py` to list every model id your key can use and to check that each configured task model answers (add `--provider gemini` or `--provider orcarouter` to test the other provider). A `402` error from OrcaRouter means the account has no credits.

**Using OrcaRouter without credits.** Set `MODEL_NAME=orcarouter/free` so query building, criteria drafting and chat use free upstream models. Screening still has to be pinned to one concrete model, so pick one of the free ids from `python3 test_llm.py --models` (they end in `-free`). The *Auto / Free* toggle in ⚙️ Settings does both in one click: it sets the default model to `orcarouter/free` and fills the screening model with a free id from your live model list. See `.env.example` for the optional harvester keys (`OPENALEX_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`, `SCOPUS_API_KEY`, `IEEE_API_KEY`).

---

## ⚙️ Configuring Inclusion Criteria

PrismaOwl is completely dynamic: the inclusion criteria are the only place your research field enters the system, and you define them per project. The `criteria.json` shipped in the repository is just a **sample** (from a criminology review on cybercriminal behaviour); replace it with criteria for your own topic before screening.

Three ways to define them:

1. **Web UI → Criteria tab**: let the LLM draft a set from your research question (or refine the current one with feedback) and edit any field; every change is saved automatically to `criteria.json`.
2. **CLI**: `python3 main.py criteria --topic "…" [--feedback "…"] [--count 3]` writes `criteria.json` after showing you the proposal.
3. **By hand**: edit `criteria.json` in the project root.


For each block, you must provide:
- **`name`**: A short, human-readable identifier.
- **`definition`**: A primary description of the rule.
- **`signals`**: String patterns for the AI to understand what characteristics to actively look for.
- **`negative_indicators`**: Negative indicators that shouldn't be falsely conflated (useful for forcing high precision).

The system's entire stack—including the LLM Prompts, the CSV Export Analytics, the CLI Tools, and the Web GUI—will automatically parse your `criteria.json` and perfectly adapt to your experiment parameters without requiring any further code modifications!

---

## 🌐 Web GUI Usage (Recommended)

To launch the full interactive web experience (with the virtual environment activated):
```bash
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```
Then navigate to `http://127.0.0.1:8000` in your browser.

- **Search Strategy**: Enter your research question, click *Generate with AI*, and get concept blocks plus one query per database. Once a strategy exists the same button becomes *Refine with AI* and sends your current concepts, queries and feedback to the model; *Start over from the research question* regenerates from scratch. Edit the concept blocks and press *Rebuild from concepts* to regenerate every database query deterministically (AND between concepts, OR inside, each database's own syntax) without another LLM call. Every edit is saved automatically to `search_strategy.json` (the CLI reads the same file), so there is no Save button. A year range in the scope notes ("2010 onwards", "2015-2024") is written into the Scopus, Web of Science and arXiv queries and applied as an API filter when harvesting OpenAlex, Semantic Scholar, Crossref and IEEE. Press *Run harvest* on any database with an open API: the hits are stored straight into the corpus (deduplicated against what is already there) as a **harvest run** that remembers the query, database, date and year range, and can be downloaded as `.bib` at any time. ACM DL and Web of Science show the query to paste into their advanced search; upload their exports on the Ingestion tab.
- **Ingestion**: A dashboard shows records identified, unique records to screen, duplicates removed and the per-source breakdown, the list of harvest runs with what each contributed, and the list of duplicate records with the reason (DOI match, or normalised title + year + first author) and the record that was kept. Drag-and-drop `.bib` exports (Scopus, Web of Science, ACM…) to add them. Duplicates are stored but never screened, so the PRISMA flow numbers come straight from the corpus.
- **Criteria**: Draft or refine inclusion criteria with the LLM and edit them in place; changes save automatically to `criteria.json` (saving waits while a screening batch runs). A *Reset screening results* button is there for when the criteria change mid-project.
- **Screening**: Start the background AI task. Each result records which upstream model the router actually used.
- **Review**: Adjudicate uncertain "Maybe" cases with rapid-action buttons.
- **Reports**: Download the CSV PRISMA report.
- **Chat**: Ask questions about the included records (themes, methods, which papers mention X…). Answers cite record IDs; switch the scope to include *Maybe* or all screened records.
- Every tab ends with a **Next** button that walks you through the stages in order; `#<tab>` in the URL (e.g. `/#screen`) opens a tab directly and `#settings` opens the Settings dialog.
- **⚙️ Settings** (bottom of the sidebar): choose the LLM provider (OrcaRouter or Gemini), enter API keys, pick the model per task from the provider's live model list, set retries/timeout and harvester credentials, and switch between dark, light and system theme. Everything except the theme is written to `.env`, so the CLI sees the same configuration.

---

## 🛠️ CLI Usage Pipeline (Legacy/Scripting)

The system also retains terminal commands for headless pipeline scripting:

### Step 0: Search strategy & harvesting (optional)
```bash
# Ask the LLM for concept blocks + one query per database (saved to search_strategy.json)
python3 main.py query --topic "Effectiveness of mindfulness-based interventions on burnout in healthcare workers"
python3 main.py query --feedback "add terms for nurses and physicians; restrict to 2010 onwards"   # refine
python3 main.py query --build      # rebuild the queries from the saved concepts, no LLM call
python3 main.py query --show

# Run the saved query for a database (or pass --query) and save BibTeX
python3 main.py harvest --list-sources
python3 main.py harvest --source openalex --max 1000                 # year range taken from the scope notes
python3 main.py harvest --source openalex --year-from 2015 --year-to 2024
python3 main.py harvest --source arxiv --query 'all:"large language model" AND abs:"code review"' --output bib_files/arxiv.bib

# Draft inclusion criteria from the research question
python3 main.py criteria --topic "Effectiveness of mindfulness-based interventions on burnout in healthcare workers" --count 3
```

### Step 1: Ingestion & Deduplication
```bash
# Bulk directory ingestion
python3 main.py ingest /path/to/bib_files_directory/ --output data/deduplicated.json
```

### Step 2: AI-Powered Screening
```bash
python3 main.py screen --input data/deduplicated.json --output data/screened.json
```

### Step 3: Human-in-the-Loop Review
Review cases where the AI was uncertain ("Maybe").
```bash
python3 main.py review --input data/screened.json --output data/final_included.json
```

### Step 4: Export & Reporting
Generate a detailed CSV report for your analysis.
```bash
python3 main.py report --input data/final_included.json --output data/screening_report.csv
```

### Step 5: Ask the corpus
```bash
python3 main.py chat --input data/final_included.json                     # interactive REPL
python3 main.py chat --input data/final_included.json --ask "Which papers report a randomised controlled trial?"
```

---

## 📂 Project Structure

| File | Description |
| :--- | :--- |
| `app.py` | FastAPI backend and REST server for the Web GUI. |
| `main.py` | Central CLI entry point for headless execution. |
| `src/db.py` | SQLite adapter mapping Pydantic records to persistent disk models. |
| `src/llm.py` | OpenAI-compatible HTTP client for OrcaRouter and Gemini, provider selection, per-task model selection, error classification. |
| `src/screening.py` | Screening orchestration with transient/fatal retry semantics. |
| `src/prompts.py` | Expert-tuned, high-precision criteria gating instructions. |
| `src/search_strategy.py` | LLM search-strategy builder (`search_strategy.json`). |
| `src/harvest.py` | Database harvesters (OpenAlex, Semantic Scholar, arXiv, Crossref, Scopus, IEEE) and BibTeX writer. |
| `src/criteria_assist.py` | LLM inclusion-criteria assistant and `criteria.json` validation. |
| `src/chat.py` | Chatbot over the screened corpus. |
| `static/` | Custom vanilla HTML/CSS/JS frontend dashboard. |

---

## 📚 References

<a id="references"></a>

[1] Page M J, McKenzie J E, Bossuyt P M, Boutron I, Hoffmann T C, Mulrow C D et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews BMJ 2021; 372 :n71 doi:10.1136/bmj.n71

The PRISMA 2020 statement and flow diagram are made available by the PRISMA Group at [prisma-statement.org](https://www.prisma-statement.org/).

> **Disclaimer.** This software is an independent screening-assistant tool and is *not* endorsed by, or affiliated with, the PRISMA Group. It implements one stage of the PRISMA-recommended workflow; users remain responsible for adherence to the full reporting guideline.

---

## 🧪 Running the Tests

The test suite runs **offline** — no API key is required (the LLM client and all database HTTP calls are mocked).

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover ingestion (BibTeX parsing), deduplication, dynamic prompt generation, CSV reporting, the LLM client (both providers) and retry logic in `screen_record`, the database harvesters and BibTeX round-trip, and the search-strategy / criteria / chat helpers. The same suite runs in CI on every push and pull request (see `.github/workflows/tests.yml`).

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and the pull-request workflow, and note our [Code of Conduct](CODE_OF_CONDUCT.md). Bug reports and feature requests go to the [issue tracker](https://github.com/0xj0hannes/PrismaOwl/issues).

---

## 📝 License
Released under the MIT License — see [LICENSE](LICENSE). Intended for academic research; please cite the PRISMA references above when reporting reviews conducted with this tool.
