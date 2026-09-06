from typing import List, Dict, Tuple
from .models import Record
from .utils import normalize_string


def title_year_author_key(record: Record) -> str:
    """Fallback identity key for records that share no DOI.

    Built in one place so the lookup and the index insertion can never drift
    apart: if the two ever disagreed, every record would look new and
    deduplication would silently stop working for DOI-less records.
    """
    first_author = record.authors.split(',')[0] if record.authors else ""
    # Ingestion fills normalized_title; fall back to the raw title for records
    # built elsewhere (tests, hand-made JSON) so they never all collide on "".
    title_key = record.normalized_title or normalize_string(record.title)
    return f"{title_key}|{record.year}|{normalize_string(first_author)}"


def deduplicate_records(records: List[Record]) -> List[Record]:
    """
    Deduplicate records based on:
    1. DOI (exact match)
    2. Title + Year + First Author (exact match of normalized string)

    Known residual case: databases sometimes disagree about which part of a name
    is the surname (e.g. "You, Xia Zheng" vs "Xia, ZhengYou" for one author), so
    the first-author half of the key differs and the pair survives as two
    records. Widening the key to title + year alone would catch these, but it
    would also merge genuinely distinct papers that share a short title within a
    year, so the stricter key is kept deliberately.
    """
    # Canonical records map: {canonical_id: record}
    canonical_records: Dict[str, Record] = {}
    
    # Indexes for fast lookup
    seen_dois: Dict[str, str] = {} # doi -> canonical_id
    seen_tya: Dict[str, str] = {} # title_year_author_key -> canonical_id
    
    deduplicated_count = 0
    
    for record in records:
        is_duplicate = False
        canonical_id = None
        
        # 1. DOI Check
        if record.doi:
            clean_doi = record.doi.lower().strip()
            if clean_doi in seen_dois:
                is_duplicate = True
                canonical_id = seen_dois[clean_doi]
                reason = f"DOI match ({clean_doi})"
        
        # 2. Title + Year + First Author Check (High Confidence)
        if not is_duplicate:
            tya_key = title_year_author_key(record)
            if tya_key in seen_tya:
                is_duplicate = True
                canonical_id = seen_tya[tya_key]
                reason = "Title + Year + Author match"

        if is_duplicate:
            print(f"  Duplicate found: \"{record.title[:60]}...\"")
            print(f"    Reason: {reason}")
            print(f"    Duplicate of: {canonical_id}")
            record.is_duplicate = True
            record.duplicate_of = canonical_id
            record.duplicate_reason = reason
            deduplicated_count += 1
        else:
            # New canonical record
            canonical_records[record.id] = record
            
            # Update indexes
            if record.doi:
                seen_dois[record.doi.lower().strip()] = record.id
            
            seen_tya[title_year_author_key(record)] = record.id
            
    return list(canonical_records.values())


def split_duplicates(records: List[Record]) -> Tuple[List[Record], List[Record]]:
    """Run deduplication and return ``(canonical, duplicates)``.

    Flags are reset first so records reloaded from storage are re-evaluated
    against the current corpus. Order matters: put already-canonical records
    first so an existing canonical record can never be demoted (its id is
    referenced by screening results).
    """
    ordered = sorted(records, key=lambda r: bool(r.is_duplicate))
    for r in ordered:
        r.is_duplicate = False
        r.duplicate_of = None
        r.duplicate_reason = ""
    canonical = deduplicate_records(ordered)
    duplicates = [r for r in ordered if r.is_duplicate]
    return canonical, duplicates
