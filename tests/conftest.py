"""Shared pytest fixtures and helpers.

These tests exercise the deterministic core of the pipeline (ingestion,
deduplication, prompt generation, reporting) and the LLM-orchestration logic
in ``screen_record`` with the OrcaRouter client mocked. No network access or API
key is required to run the suite.
"""
import sys
from pathlib import Path

import pytest

# Make the project root importable so ``import src...`` works regardless of the
# directory pytest is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import Record  # noqa: E402


@pytest.fixture
def sample_criteria():
    """A minimal two-criterion configuration mirroring criteria.json."""
    return {
        "IC1": {
            "name": "Topic relevance",
            "definition": "The study is about the review topic.",
            "signals": "keyword a, keyword b",
            "negative_indicators": "off-topic surveys",
        },
        "IC2": {
            "name": "Human element",
            "definition": "The study addresses human behaviour.",
            "signals": "psychology, sociology",
            "negative_indicators": "purely technical work",
        },
    }


def make_record(**overrides):
    """Build a Record with sensible defaults, overridable per field."""
    defaults = dict(
        id="rec1",
        title="An Example Study of Behaviour",
        abstract="We study human behaviour.",
        authors="Doe, Jane and Smith, John",
        year="2024",
        doi="10.1000/example",
    )
    defaults.update(overrides)
    return Record(**defaults)


@pytest.fixture
def record_factory():
    return make_record
