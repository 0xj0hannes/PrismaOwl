import argparse
import json
import os
import sys
from src.config import load_config

def main():
    parser = argparse.ArgumentParser(description="PrismaOwl: LLM-assisted PRISMA title/abstract screening")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Ingestion command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest and deduplicate BibTeX files")
    ingest_parser.add_argument("input_file", help="Path to input BibTeX file")
    ingest_parser.add_argument("--output", default="data/deduplicated.json", help="Path to output deduplicated dataset")

    # Screening command
    screen_parser = subparsers.add_parser("screen", help="Screen records using LLM")
    screen_parser.add_argument("--input", default="data/deduplicated.json", help="Path to deduplicated dataset")
    screen_parser.add_argument("--output", default="data/screened.json", help="Path to output screened dataset")

    # Review command
    review_parser = subparsers.add_parser("review", help="Human-in-the-loop review interface")
    review_parser.add_argument("--input", default="data/screened.json", help="Path to screened dataset")
    review_parser.add_argument("--output", default="data/final_included.json", help="Path to final dataset")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate PRISMA statistics and CSV")
    report_parser.add_argument("--input", default="data/final_included.json", help="Path to final dataset")
    report_parser.add_argument("--output", default="data/screening_report.csv", help="Path to output CSV")

    args = parser.parse_args()

    # Ensure output directory exists for all commands that have an output argument
    if hasattr(args, 'output') and args.output:
        directory = os.path.dirname(args.output)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

    if args.command == "ingest":
        from src.ingestion import load_bibtex
        from src.deduplication import deduplicate_records
        from src.models import Dataset
        
        input_path = args.input_file
        if not os.path.exists(input_path):
            print(f"Error: Input path '{input_path}' does not exist.")
            sys.exit(1)

        all_records = []
        
        if os.path.isdir(input_path):
            print(f"Ingesting all .bib files from directory: {input_path}...")
            bib_files = [os.path.join(input_path, f) for f in os.listdir(input_path) if f.lower().endswith('.bib')]
            if not bib_files:
                print(f"Error: No .bib files found in {input_path}")
                sys.exit(1)
            
            for bib_file in bib_files:
                print(f"  Loading {bib_file}...")
                file_records = load_bibtex(bib_file)
                print(f"    Found {len(file_records)} records.")
                all_records.extend(file_records)
        else:
            print(f"Ingesting from file: {input_path}...")
            all_records = load_bibtex(input_path)
            
        print(f"Total records loaded: {len(all_records)}")
        deduped = deduplicate_records(all_records)
        print(f"Deduplicated to {len(deduped)} unique records.")
        
        # Save structural data
        dataset = Dataset(records=deduped)
        with open(args.output, 'w') as f:
            f.write(dataset.model_dump_json(indent=2))
        print(f"Saved to {args.output}")

    elif args.command == "screen":
        if not os.path.exists(args.input):
            print(f"Error: Input file '{args.input}' not found. Run 'ingest' first.")
            sys.exit(1)

        config = load_config()
        if not config.get("GEMINI_API_KEY"):
            print("Error: GEMINI_API_KEY not found in .env file.")
            print("Please create a .env file with GEMINI_API_KEY=your_key_here")
            sys.exit(1)

        with open(args.input, 'r') as f:
            data = json.load(f)
            records_data = data.get("records", [])
            from src.models import Record, ScreeningResult
            from src.screening import batch_screen
            records = [Record(**r) for r in records_data]
            
        # Check if output already exists to resume
        already_screened = {}
        if os.path.exists(args.output):
            print(f"Found existing output {args.output}, attempting to resume...")
            try:
                with open(args.output, 'r') as f:
                    existing_data = json.load(f)
                    res_data = existing_data.get("screening_results", {})
                    already_screened = {rid: ScreeningResult(**res) for rid, res in res_data.items()}
                    print(f"  Loaded {len(already_screened)} existing results.")
            except Exception as e:
                print(f"  Could not load existing results: {e}. Starting fresh.")

        # Prepare output data structure
        output_data = data
        if "screening_results" not in output_data:
            output_data["screening_results"] = {}
        
        # Merge existing results into output_data if any
        for rid, res in already_screened.items():
            output_data["screening_results"][rid] = res.model_dump()

        try:
            count = 0
            for rid, res in batch_screen(records, already_screened):
                output_data["screening_results"][rid] = res.model_dump()
                
                # Save after every record for maximum safety
                with open(args.output, 'w') as f:
                    json.dump(output_data, f, indent=2)
                
                # Check for failure in result
                if "Failed after" in res.notes:
                    print(f"  Note: Record {rid} failed screening but progress was saved.")
                
                count += 1
            
            print(f"Screening session complete. Processed {count} new/updated records.")
        except KeyboardInterrupt:
            print("\nInterrupted by user. Progress has been saved.")
        except Exception as e:
            print(f"\nAn error occurred during screening: {e}")
            print("Progress has been saved up to the last successful record.")
        
        print(f"Final results saved to {args.output}")

    elif args.command == "review":
        if not os.path.exists(args.input):
            print(f"Error: Input file '{args.input}' not found. Run 'screen' first.")
            sys.exit(1)
            
        print(f"Starting review interface for {args.input}...")
        from src.review import run_review_loop
        run_review_loop(args.input, args.output)

    elif args.command == "report":
        if not os.path.exists(args.input):
            print(f"Error: Input file '{args.input}' not found. Run 'review' first.")
            sys.exit(1)
            
        print(f"Generating report from {args.input}...")
        from src.reporting import generate_report
        generate_report(args.input, args.output)

if __name__ == "__main__":
    main()
