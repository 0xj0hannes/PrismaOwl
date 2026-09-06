from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class Record(BaseModel):
    id: str
    title: str
    abstract: str = ""
    authors: str = ""
    year: Optional[str] = None
    doi: Optional[str] = None
    source_file: str = ""
    harvest_id: Optional[str] = None   # set when the record came from an API harvest run
    raw_data: Dict[str, Any] = Field(default_factory=dict)
    
    # Deduplication fields
    normalized_title: str = ""
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None # ID of the canonical record
    duplicate_reason: str = ""         # "DOI match (...)" or "Title + Year + Author match"

class CriterionResult(BaseModel):
    score: float = 0.0
    evidences: List[str] = Field(default_factory=list)
    rationale: str = ""

class ScreeningResult(BaseModel):
    record_id: str
    decision: str # "Include", "Exclude", "Maybe"
    criteria: Dict[str, CriterionResult] = Field(default_factory=dict)
    unmet_criteria: str = ""
    notes: str = ""
    timestamp: str = ""
    model_version: str = ""

class Dataset(BaseModel):
    records: List[Record] = Field(default_factory=list)
    screening_results: Dict[str, ScreeningResult] = Field(default_factory=dict)
