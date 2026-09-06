import json
import pandas as pd
from typing import Dict, Any
import sys
from .config import load_config

def generate_report(input_file: str, output_csv: str):
    print(f"Loading data from {input_file}...")
    try:
        with open(input_file, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: File {input_file} not found.")
        return

    records = {r["id"]: r for r in data.get("records", [])}
    results = data.get("screening_results", {})
    
    # Calculate PRISMA stats
    n_imported = len(records)
    n_deduplicated = len([r for r in records.values() if not r.get("is_duplicate")])
    
    n_screened = len(results)
    n_included = 0
    n_excluded = 0
    n_maybe = 0
    n_human_reviewed = 0
    
    unmet_counts = {}
    
    # Load config to get active criteria, although we can also infer from data
    config = load_config()
    active_criteria = config.get("CRITERIA", {})
    criteria_keys = list(active_criteria.keys())
    
    rows = []
    
    for rid, record in records.items():
        if record.get("is_duplicate"):
            continue
            
        result = results.get(rid, {})
        decision = result.get("final_decision") or result.get("decision", "Not Screened")
        
        if decision == "Include":
            n_included += 1
        elif decision == "Exclude":
            n_excluded += 1
        elif decision == "Maybe":
            n_maybe += 1
            
        if decision in ["Exclude", "Maybe"]:
            unmet = result.get("unmet_criteria", "")
            if not unmet:
                unmet = "None/Other"
            unmet_counts[unmet] = unmet_counts.get(unmet, 0) + 1
            
        if result.get("human_reviewed"):
            n_human_reviewed += 1
            
        row = {
            "ID": rid,
            "Title": record.get("title"),
            "Year": record.get("year"),
            "Authors": record.get("authors"),
            "DOI": record.get("doi"),
            "Decision": decision,
            "Unmet_Criteria": result.get("unmet_criteria", ""),
            "Human_Reviewed": result.get("human_reviewed", False),
            "Notes": result.get("notes"),
            "Model_Version": result.get("model_version", ""),
            "Strictness": result.get("strictness", ""),
        }
        
        # Unroll dynamic criteria
        c_results = result.get("criteria", {})
        for c_key in criteria_keys:
            c_data = c_results.get(c_key, {})
            row[f"{c_key}_Score"] = c_data.get("score")
            evs = c_data.get("evidences", [])
            row[f"{c_key}_Evidences"] = "; ".join(evs) if isinstance(evs, list) else ""
            row[f"{c_key}_Rationale"] = c_data.get("rationale")
            
        rows.append(row)
        
    print("\n" + "="*40)
    print("PRISMA FLOW STATISTICS")
    print("="*40)
    print(f"Records Identified: {n_imported}")
    print(f"Records Deduplicated: {n_deduplicated}")
    print(f"Records Screened: {n_screened}")
    print(f" - Included: {n_included}")
    print(f" - Excluded: {n_excluded}")
    print(f" - Maybe (Pending): {n_maybe}")
    print(" Unmet Criteria for Excluded/Maybe:")
    for key, count in sorted(unmet_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"   * {key}: {count}")
    print(f"Human Reviewed: {n_human_reviewed}")
    print("="*40 + "\n")
    
    df = pd.DataFrame(rows)
    # Ensure specific column ordering: core fields, then criteria, then the rest
    core_cols = ["ID", "Title", "Year", "Authors", "DOI", "Decision", "Unmet_Criteria"]
    crit_cols = []
    for c_key in criteria_keys:
        crit_cols.extend([f"{c_key}_Score", f"{c_key}_Evidences", f"{c_key}_Rationale"])
    tail_cols = ["Human_Reviewed", "Notes"]
    
    final_cols = [c for c in core_cols + crit_cols + tail_cols if c in df.columns]
    
    df = df[final_cols]
    df.to_csv(output_csv, index=False)
    print(f"Detailed CSV report saved to {output_csv}")
