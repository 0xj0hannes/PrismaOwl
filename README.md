# 🦉 PrismaOwl

**PrismaOwl** is a high-precision, LLM-assisted screening assistant designed for **Systematic Literature Reviews**. This system leverages the Google Gemini API to accelerate the title/abstract screening phase of the PRISMA 2020 reporting guideline [[1]](#references), specifically optimized for research on **cybercriminal behavior** and the **human element** in cybersecurity.

The tool implements the *screening* stage described in PRISMA 2020 [[1]](#references); it does **not** replace any other PRISMA stage (protocol registration, eligibility assessment of full texts, risk-of-bias appraisal, synthesis, or reporting). All AI-suggested decisions are user-configurable inclusion criteria, fully logged for audit, and surfaced for human adjudication in the "Maybe" review queue.

---

## 🎯 Statement of Need

The title/abstract screening phase of a systematic review is laborious and prone to reviewer fatigue: thousands of records must be judged against predefined inclusion criteria. Existing platforms such as [Rayyan](https://www.rayyan.ai/) and [ASReview](https://asreview.nl/) accelerate this with collaboration features and active-learning classifiers, but those classifiers require an accumulated set of human labels before they help, and they typically surface a single relevance ranking rather than an explicit, criterion-by-criterion justification.

This tool takes a complementary, zero-shot approach: it sends each record to an LLM together with your natural-language inclusion criteria and returns, **for every criterion**, a score, quoted supporting evidence, and a rationale — no training labels required. The model only triages; a human adjudicates every ambiguous (`Maybe`) case, and all prompts and raw responses are logged so each AI suggestion remains auditable. It is built for researchers, librarians, and students who want to cut manual screening effort without giving up the transparency and human oversight the methodology demands.

---

## ✨ Key Features

- 🖥 **Premium Web Dashboard**: A sleek, dark-mode GUI natively powered by FastAPI for seamless visual ingestion, screening, and human-in-the-loop review.
- 🔬 **High-Precision AI Screening**: Strictly engineered to optimize precision—enforcing an objective standard that requires explicit evidence of the human element.
- 💾 **Hybrid Persistence**: Robust JSON databasing for CLI scripts and heavy-duty global deduplication SQLite persistence (`data/prisma.db`) for web operations.
- 🤝 **Interactive Human-in-the-loop**: Rapid-fire visual interface for the manual review of ambiguous AI decisions ("Maybe" cases).
- 🔄 **Stateful Execution**: Automatic background save-states guarantee you will never lose screening work from API limits or closed tabs.
- 📂 **Bulk BibTeX Uploads**: Visually drag-and-drop massive batches of `.bib` records directly into the unified screening queue with instant duplicate rejection.

---

## 🚀 Setup & Configuration

### 1. Prerequisites
- Python 3.8+
- [Google AI Studio API Key](https://aistudio.google.com/)

**Option A — Google AI Studio (recommended, no credit card required):**
Sign in to [Google AI Studio](https://aistudio.google.com/) with a Google account and click **Get API key** → **Create API key**. As a new user, AI Studio automatically provisions a default Google Cloud project for you, and the key gets free-tier access immediately — no billing account needed.

**Option B — Google Cloud Console (if you already manage a GCP project):**
Open [Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials), enable the **Generative Language API** on your project, then create an API key there. This still uses the same free-tier Gemini Developer API — don't confuse it with **Vertex AI**, which is Google Cloud's separate enterprise offering and is billed, not free.

> **Can't create a key?** If Google AI Studio refuses to generate an API key (e.g. it appears to require a paid plan), this is usually caused by an unverified Google account age, not billing. Verify your age on your [Google Account](https://myaccount.google.com/) (a government ID may be requested) and retry — see [discussion #5](https://github.com/0xj0hannes/PrismaOwl/discussions/5).

> **Note on key types:** Keys created in AI Studio today are issued as the newer *auth key* type, so a fresh key already meets current requirements. If you are reusing an older *Standard* key, migrate it — [Google's docs](https://ai.google.dev/gemini-api/docs/api-key) state the Gemini API will reject Standard keys from September 2026, and unrestricted keys left dormant for an extended period are blocked (shown with a **Blocked** tag in AI Studio). If screening suddenly fails on a key that used to work, check this first.

### 2. Installation
```bash
git clone <repository-url>
cd PrismaOwl
pip install -r requirements.txt
```

### 3. Environment Setup
Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_api_key_here
MODEL_NAME=gemini-3.5-flash
MAX_RETRIES=3
```

> **Note:** Google deprecates older Gemini models over time. If screening fails
> with a `404` error such as *"This model is no longer available to new users"*,
> run `python3 test_gemini.py` to list the models available to your API key and
> set `MODEL_NAME` accordingly.

---

## ⚙️ Configuring Inclusion Criteria

PrismaOwl is completely dynamic and allows you to configure arbitrary inclusion criteria limits for different projects!

To modify the criteria used by the AI to evaluate your literature, edit the `criteria.json` file located in the root directory. You can add or rename custom criteria endpoints simply by editing the JSON structure. 

For each block, you must provide:
- **`name`**: A short, human-readable identifier.
- **`definition`**: A primary description of the rule.
- **`signals`**: String patterns for the AI to understand what characteristics to actively look for.
- **`negative_indicators`**: Negative indicators that shouldn't be falsely conflated (useful for forcing high precision).

The system's entire stack—including the LLM Prompts, the CSV Export Analytics, the CLI Tools, and the Web GUI—will automatically parse your `criteria.json` and perfectly adapt to your experiment parameters without requiring any further code modifications!

---

## 🌐 Web GUI Usage (Recommended)

To launch the full interactive web experience:
```bash
.venv/bin/uvicorn app:app --reload --host 127.0.0.1 --port 8000
```
Then navigate to `http://127.0.0.1:8000` in your browser.

- **Ingestion**: Drag-and-drop multiple `.bib` files. The backend automatically unpacks, normalizes, and globally deduplicates the contents into the master SQL database.
- **Screening**: Start the silent AI automation task. The LLM processes criteria in the background and populates live statistics.
- **Review**: Dynamically filter out uncertain "Maybe" cases using integrated rapid-action toggle buttons.
- **Export**: Instantly download finalized CSV PRISMA records for mapping and external publication.

---

## 🛠️ CLI Usage Pipeline (Legacy/Scripting)

The system also retains terminal commands for headless pipeline scripting:

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

---

## 📂 Project Structure

| File | Description |
| :--- | :--- |
| `app.py` | FastAPI backend and REST server for the Web GUI. |
| `main.py` | Central CLI entry point for headless execution. |
| `src/db.py` | SQLite adapter mapping Pydantic records to persistent disk models. |
| `src/screening.py` | LLM orchestration natively utilizing the updated `google-genai` SDK. |
| `src/prompts.py` | Expert-tuned, high-precision criteria gating instructions. |
| `static/` | Custom vanilla HTML/CSS/JS frontend dashboard. |

---

## 📚 References

<a id="references"></a>

[1] Page M J, McKenzie J E, Bossuyt P M, Boutron I, Hoffmann T C, Mulrow C D et al. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews BMJ 2021; 372 :n71 doi:10.1136/bmj.n71

The PRISMA 2020 statement and flow diagram are made available by the PRISMA Group at [prisma-statement.org](https://www.prisma-statement.org/).

> **Disclaimer.** This software is an independent screening-assistant tool and is *not* endorsed by, or affiliated with, the PRISMA Group. It implements one stage of the PRISMA-recommended workflow; users remain responsible for adherence to the full reporting guideline.

---

## 🧪 Running the Tests

The test suite runs **offline** — no Gemini API key is required (the LLM client is mocked).

```bash
pip install -r requirements-dev.txt
pytest
```

Tests cover ingestion (BibTeX parsing), deduplication, dynamic prompt generation, CSV reporting, and the LLM-orchestration/retry logic in `screen_record`. The same suite runs in CI on every push and pull request (see `.github/workflows/tests.yml`).

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and the pull-request workflow, and note our [Code of Conduct](CODE_OF_CONDUCT.md). Bug reports and feature requests go to the [issue tracker](https://github.com/0xj0hannes/PrismaOwl/issues).

---

## 📝 License
Released under the MIT License — see [LICENSE](LICENSE). Intended for academic research; please cite the PRISMA references above when reporting reviews conducted with this tool.
