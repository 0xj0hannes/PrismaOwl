from typing import List, Dict, Any
from .models import Record, ScreeningResult
import os
import json
import random

def load_screening_results(file_path: str) -> Dict[str, Any]:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

def save_results(data: Dict[str, Any], file_path: str):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def run_review_loop(input_file: str, output_file: str):
    # Resume capability: If output file exists, load it to continue
    if os.path.exists(output_file):
        print(f"Resuming from existing review file: {output_file}")
        data = load_screening_results(output_file)
    else:
        print(f"Loading fresh data from {input_file}...")
        if not os.path.exists(input_file):
            print(f"Error: File {input_file} not found.")
            return
        data = load_screening_results(input_file)

    screened_results = data.get("screening_results", {})
    records_list = data.get("records", [])
    records = {r["id"]: r for r in records_list}
    
    # Identify items to review
    to_review = []
    
    # 1. All "Maybes" (LLM was uncertain)
    maybes = [rid for rid, result in screened_results.items() if result["decision"] == "Maybe"]
    to_review.extend(maybes)
            

        
    # Count how many of these are already done
    already_reviewed = sum(1 for rid in to_review if screened_results.get(rid, {}).get("human_reviewed"))
    
    print(f"Total target: {len(to_review)} records to review.")
    print(f"Progress: {already_reviewed}/{len(to_review)} already completed.")
    
    if already_reviewed == len(to_review):
        print("All matching records have already been reviewed.")
        return

    reviewed_this_session = 0
    
    try:
        for rid in to_review:
            record = records.get(rid)
            result = screened_results.get(rid)
            
            if not record or not result:
                continue
                
            # Skip records already handled in previous sessions
            if result.get('human_reviewed'):
                continue
            
            print("\n" + "="*80)
            print(f"Record [{already_reviewed + reviewed_this_session + 1}/{len(to_review)}]")
            print(f"ID: {rid}")
            print(f"Title: {record.get('title')}")
            print(f"LLM Decision: {result.get('decision')}")
            print(f"Unmet Criteria: {result.get('unmet_criteria', '')}")
            
            criteria_results = result.get('criteria', {})
            for c_key, c_val in criteria_results.items():
                print(f"{c_key} Rationale: {c_val.get('rationale')}")
                evs = c_val.get('evidences', [])
                print(f"{c_key} Evidences: {', '.join(evs) if isinstance(evs, list) else ''}")
                
            print(f"Notes: {result.get('notes')}")
            print("-"*80)
            print(f"Abstract: {record.get('abstract')}")
            print("="*80)
            
            while True:
                choice = input("Your Decision (i=Include, e=Exclude, s=Skip, q=Quit): ").lower()
                if choice == 'q':
                    print("Saving progress and quitting...")
                    save_results(data, output_file)
                    return
                elif choice == 'i':
                    result['final_decision'] = "Include"
                    result['human_reviewed'] = True
                    reviewed_this_session += 1
                    break
                elif choice == 'e':
                    # Allow user to manually type the keys separated by commas if needed, or none
                    uc_choice = input(f"Unmet Criteria? (e.g., {', '.join(criteria_results.keys())}, or combinations, n=None): ").strip()
                    if uc_choice.lower() == 'n':
                        result['unmet_criteria'] = 'None'
                    else:
                        result['unmet_criteria'] = uc_choice if uc_choice else 'None'
                        
                    result['final_decision'] = "Exclude"
                    result['human_reviewed'] = True
                    reviewed_this_session += 1
                    break
                elif choice == 's':
                    print("Skipping...")
                    break
                else:
                    print("Invalid input.")
            
            # Auto-save after every decision for maximum safety
            save_results(data, output_file)
                
    except KeyboardInterrupt:
        print("\nInterrupted. Saving progress...")
    
    save_results(data, output_file)
    print(f"Session finished. Reviewed {reviewed_this_session} new records. Total saved to {output_file}.")
