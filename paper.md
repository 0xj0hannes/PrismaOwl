---
title: 'PrismaOwl: a human-in-the-loop tool for LLM-assisted title and abstract screening in systematic reviews'
tags:
  - Python
  - systematic review
  - PRISMA
  - large language models
  - literature screening
  - evidence synthesis
authors:
  - name: FIRST AUTHOR FULL NAME
    orcid: 0000-0000-0000-0000
    corresponding: true
    affiliation: 1
  - name: SECOND AUTHOR FULL NAME
    orcid: 0000-0000-0000-0000
    affiliation: 2
affiliations:
  - name: Affiliation of the first author, City, Country
    index: 1
  - name: Affiliation of the second author, City, Country
    index: 2
date: 29 June 2026
bibliography: paper.bib
---

# Summary

Systematic literature reviews are a cornerstone of evidence-based research, but
the title and abstract screening phase is laborious: reviewers must read and
judge thousands of records against predefined inclusion criteria.
**PrismaOwl** is an open-source Python tool that
accelerates this single phase of the PRISMA 2020 reporting workflow
[@page2021prisma] by using a large language model (LLM) to produce a structured,
per-criterion, evidence-backed recommendation for each record, while keeping a
human reviewer firmly in control of every final decision.

The tool ingests BibTeX exports from bibliographic databases, deduplicates them
(by DOI and by a normalized title/year/first-author key), and sends each
candidate record to an LLM (through the OrcaRouter gateway, so any hosted model can be used, or directly to the Google Gemini API) with a JSON-mode prompt generated from
a user-supplied set of inclusion criteria. For every criterion the model returns
a numeric score, a list of supporting evidence phrases, and a free-text
rationale; these are aggregated into an overall `Include`, `Exclude`, or `Maybe`
decision. Ambiguous (`Maybe`) records are routed to a human-in-the-loop review
queue, and the final corpus is exported as a CSV report alongside PRISMA flow
statistics. Every prompt and raw model response is logged to disk so that the
basis for each AI suggestion remains auditable — a requirement for the
transparency expected of systematic reviews.

The software is offered through two interchangeable interfaces over a shared
core: a FastAPI web dashboard for interactive use and a command-line pipeline
for headless, scriptable runs. The inclusion criteria are fully data-driven: a
single `criteria.json` file reshapes the model prompts, the parsing logic, and
the CSV output columns, so the tool can be retargeted to any review topic
without code changes.

# Statement of need

The volume of literature returned by database searches routinely exceeds what a
small research team can screen by hand, and screening is both time-consuming and
prone to reviewer fatigue. Established screening platforms such as Rayyan
[@ouzzani2016rayyan] and ASReview [@vandeschoot2021asreview] address this with
collaboration features and active-learning classifiers trained on reviewer
labels, while commercial platforms such as Covidence provide hosted workflows.
These tools are valuable, but they generally (i) require an accumulated set of
human labels before their machine assistance becomes useful, and (ii) surface a
relevance ranking or probability rather than an explicit, criterion-by-criterion
justification that a reviewer can inspect and contest.

Recent work has shown that instruction-following LLMs can screen records
zero-shot against natural-language inclusion criteria with promising agreement
against human reviewers [@syriani2023llmscreening]. PrismaOwl
operationalizes this capability in a way that is purpose-built
for transparent, reproducible review practice. Its distinguishing features are:

- **Per-criterion, evidence-backed output.** Rather than a single relevance
  score, each record receives a structured verdict for every inclusion
  criterion, with quoted evidence and a rationale, making the recommendation
  inspectable and challengeable.
- **Zero-shot, no training labels required.** Screening starts immediately from
  natural-language criteria, removing the cold-start labelling burden.
- **Human-in-the-loop by design.** The model never finalizes decisions; it
  triages, and a human adjudicates the ambiguous cases.
- **Auditability.** Complete prompt and response logs preserve the provenance of
  every AI suggestion.
- **Reproducible, retargetable configuration.** All criteria live in a single
  declarative file that drives the entire stack.
- **Resilience.** Both pipelines persist results incrementally and resume after
  interruption or API rate-limiting.

The tool is aimed at researchers, librarians, and students conducting systematic
reviews who want to reduce manual screening effort without surrendering the
transparency and human oversight that the methodology demands. It implements
*only* the screening stage of PRISMA 2020 and is not a substitute for protocol
registration, full-text eligibility assessment, risk-of-bias appraisal, or
synthesis; nor is it affiliated with or endorsed by the PRISMA Group.

# Implementation

The software is written in Python (3.8+) and organized as a shared domain core
(`src/`) driven by two thin entry points. Records flow through five stages:
(1) **ingestion**, which parses BibTeX into validated `pydantic` data models and
normalizes titles; (2) **deduplication**, by exact DOI match and by a composite
normalized `title | year | first-author` key; (3) **screening**, which builds a
JSON-mode prompt per record from `criteria.json` and queries the LLM through OrcaRouter or the Gemini API,
distinguishing transient errors (rate limits, service unavailability), which are
retried with exponential backoff, from persistent ones; (4) **review**, the
human adjudication of `Maybe` records; and (5) **reporting**, which flattens
results to a CSV with one score/evidence/rationale column triplet per criterion
and prints PRISMA flow counts. A command-line pipeline (`main.py`) persists
state as JSON, while a FastAPI application (`app.py`) provides an interactive web
dashboard backed by SQLite; both are designed to resume safely after
interruption. The criteria keys propagate through prompt construction, response
parsing, and report columns, so adding or renaming a criterion reconfigures the
whole pipeline without code changes. An automated test suite covers the
deterministic stages and the screening logic with the LLM mocked, and runs in
continuous integration.

# AI usage disclosure

Generative AI tools were used extensively in the creation of this software and
its submission, as permitted by the JOSS policy on generative AI. Specifically,
Claude (Anthropic; Claude Opus 4.8) was used to:

- **generate the source code** of the software itself (`src/`, `app.py`,
  `main.py`, and the web frontend under `static/`), under the authors'
  direction and iterative specification;
- draft this paper (`paper.md`) and its starter bibliography (`paper.bib`);
- scaffold the automated test suite (`tests/`) and the continuous-integration
  workflow; and
- write the `CONTRIBUTING` and `CODE_OF_CONDUCT` files and revise sections of
  the `README`.

The authors specified the requirements, methodology, and screening design;
directed and iterated on the AI-generated code; and reviewed, edited, tested,
and validated all AI-assisted outputs. The authors take full responsibility for
the accuracy, originality, licensing, and ethical and legal compliance of all
submitted materials.

# Acknowledgements

We acknowledge the PRISMA Group for the PRISMA 2020 statement on which the
screening workflow is based.

# References
