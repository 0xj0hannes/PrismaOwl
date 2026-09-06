import argparse
import json
import os
import sys
from src.config import load_config

DEPRECATION_NOTICE = ("[deprecated] The command-line pipeline (main.py) is deprecated and will be removed "
                      "in a future release. Configuration and every review stage are available in the web "
                      "interface: python -m uvicorn app:app --host 127.0.0.1 --port 8000")


def main():
    print(DEPRECATION_NOTICE, file=sys.stderr)
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

    # --- SoK / PRISMA extension commands -------------------------------------
    # Search-strategy builder (LLM)
    query_parser = subparsers.add_parser("query", help="Build/refine database search queries with the LLM")
    query_parser.add_argument("--topic", default="", help="Research question / topic (omit to reuse the saved one)")
    query_parser.add_argument("--feedback", default="", help="Feedback to refine the saved strategy")
    query_parser.add_argument("--show", action="store_true", help="Only print the saved strategy")
    query_parser.add_argument("--build", action="store_true",
                              help="Rebuild the per-database queries from the saved concept blocks "
                                   "(and the year range in the scope notes) without calling the LLM")
    query_parser.add_argument("--output", default=None, help="Path to the strategy JSON (default: search_strategy.json)")

    # Database harvesting
    harvest_parser = subparsers.add_parser("harvest", help="Run a query against a database API and save BibTeX")
    harvest_parser.add_argument("--source", required=False, help="openalex | semantic_scholar | arxiv | crossref | scopus | ieee")
    harvest_parser.add_argument("--query", default=None, help="Query string (default: the saved strategy's query for --source)")
    harvest_parser.add_argument("--max", type=int, default=500, help="Maximum records to fetch (default 500)")
    harvest_parser.add_argument("--output", default=None, help="Output .bib path (default: data/harvest/<source>_<timestamp>.bib)")
    harvest_parser.add_argument("--list-sources", action="store_true", help="List sources and their availability")
    harvest_parser.add_argument("--year-from", type=int, default=None,
                                help="Earliest publication year (default: parsed from the saved scope notes)")
    harvest_parser.add_argument("--year-to", type=int, default=None,
                                help="Latest publication year (default: parsed from the saved scope notes)")

    # Inclusion-criteria assistant (LLM)
    crit_parser = subparsers.add_parser("criteria", help="Draft/refine inclusion criteria with the LLM")
    crit_parser.add_argument("--topic", default="", help="Research question / topic (omit to reuse the saved strategy's)")
    crit_parser.add_argument("--feedback", default="", help="Feedback to refine the current criteria")
    crit_parser.add_argument("--count", type=int, default=None, help="Number of criteria to propose")
    crit_parser.add_argument("--fresh", action="store_true", help="Ignore the current criteria.json when generating")
    crit_parser.add_argument("--show", action="store_true", help="Only print the current criteria")
    crit_parser.add_argument("--output", default=None, help="Where to write the criteria (default: criteria.json)")
    crit_parser.add_argument("--yes", action="store_true", help="Overwrite without confirmation")

    # Chat over the screened corpus
    chat_parser = subparsers.add_parser("chat", help="Ask questions about the included records")
    chat_parser.add_argument("--input", default="data/final_included.json", help="Screened/reviewed dataset")
    chat_parser.add_argument("--scope", default="included", choices=["included", "included_maybe", "all_screened"],
                             help="Which records the bot can see (default: included)")
    chat_parser.add_argument("--ask", default=None, help="Ask a single question and exit instead of starting a REPL")

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
        if not config.get("LLM_API_KEY"):
            from src.llm import provider_settings
            ps = provider_settings(config)
            print(f"Error: {ps['key_env']} not found in .env file (LLM provider: {ps['label']}).")
            print(f"Please create a .env file with {ps['key_env']}=... (see .env.example). "
                  "Set LLM_PROVIDER=orcarouter or LLM_PROVIDER=gemini to choose the provider.")
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

        # Reproducibility: screening must be pinned to one concrete model, and
        # it must be the model that produced any results we are resuming.
        from src.screening import check_screening_setup
        from src.llm import ScreeningModelError
        try:
            print(f"Screening model: {check_screening_setup(already_screened)}")
        except ScreeningModelError as e:
            print(f"Error: {e}")
            sys.exit(1)

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

    elif args.command == "query":
        from src.config import load_search_strategy, save_search_strategy, SEARCH_STRATEGY_PATH
        from src.search_strategy import (generate_strategy, format_strategy, normalize_strategy,
                                         build_queries, parse_year_range, format_year_range)
        from src.llm import LLMError
        path = args.output or SEARCH_STRATEGY_PATH
        current = load_search_strategy(path)
        if args.show:
            if not current:
                print(f"No strategy saved at {path}. Run: python3 main.py query --topic \"...\"")
                sys.exit(1)
            print(format_strategy(current))
            return
        if args.build:
            if not current:
                print(f"No strategy saved at {path}. Run 'query --topic' first or create the file by hand.")
                sys.exit(1)
            strategy = normalize_strategy(current)
            if not any(c["terms"] for c in strategy["concepts"]):
                print("Error: the saved strategy has no concept terms to build queries from.")
                sys.exit(1)
            strategy["queries"] = build_queries(strategy["concepts"], strategy["scope_notes"])
            save_search_strategy(strategy, path)
            years = format_year_range(parse_year_range(strategy["scope_notes"]))
            print(format_strategy(strategy))
            print(f"\nRebuilt {len(strategy['queries'])} queries from {len(strategy['concepts'])} concepts "
                  f"(no LLM call){' with year range ' + years if years else ''}. Saved to {path}.")
            return
        topic = args.topic or current.get("research_question", "")
        if not topic:
            print("Error: --topic is required the first time (no saved strategy found).")
            sys.exit(1)
        print("Asking the LLM to build the search strategy...")
        try:
            strategy = generate_strategy(topic, current=current or None, feedback=args.feedback)
        except (LLMError, ValueError) as e:
            print(f"Error: {getattr(e, 'user_message', e)}")
            sys.exit(1)
        save_search_strategy(strategy, path)
        print(format_strategy(strategy))
        print(f"\nSaved to {path}. Edit it by hand or via the web UI, then run 'harvest'.")

    elif args.command == "harvest":
        from src.harvest import harvest_to_file, list_sources, HarvestError, PROVIDERS
        if args.list_sources:
            for s_ in list_sources():
                state = "manual export only" if s_["manual"] else ("ready" if s_["available"] else "needs API key")
                print(f"  {s_['key']:<18} {s_['label']:<22} {state}")
                print(f"  {'':<18} {s_['note']}")
            return
        if not args.source:
            print("Error: --source is required (or use --list-sources).")
            sys.exit(1)
        from src.config import load_search_strategy
        from src.search_strategy import parse_year_range, format_year_range
        saved = load_search_strategy()
        query = args.query
        if not query:
            query = (saved.get("queries") or {}).get(args.source, "")
            if not query:
                print(f"Error: no saved query for '{args.source}'. Pass --query or run 'query' first.")
                sys.exit(1)
            print(f"Using saved query for {args.source}:\n  {query}")
        years = (args.year_from, args.year_to)
        if years == (None, None):
            years = parse_year_range(saved.get("scope_notes", ""))
        if years != (None, None):
            print(f"Publication years: {format_year_range(years)}")

        def progress(n, total):
            print(f"  fetched {n}" + (f" / {total}" if total is not None else "") + "...")

        try:
            summary = harvest_to_file(args.source, query, output=args.output, max_results=args.max,
                                      progress=progress, years=years)
        except HarvestError as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"Saved {summary['count']} records ({summary['with_abstract']} with abstracts) to {summary['file']}")
        print(f"Next: python3 main.py ingest {summary['file']} --output data/deduplicated.json")

    elif args.command == "criteria":
        from src.config import load_criteria, save_criteria, load_search_strategy, CRITERIA_PATH
        from src.criteria_assist import generate_criteria, format_criteria
        from src.llm import LLMError
        path = args.output or CRITERIA_PATH
        current = load_criteria(path)
        if args.show:
            print(format_criteria(current) if current else f"No criteria found at {path}.")
            return
        topic = args.topic or load_search_strategy().get("research_question", "")
        if not topic:
            print("Error: --topic is required (no saved search strategy to take it from).")
            sys.exit(1)
        print("Asking the LLM to draft inclusion criteria...")
        try:
            proposed = generate_criteria(topic, current=None if args.fresh else (current or None),
                                         feedback=args.feedback, count=args.count)
        except (LLMError, ValueError) as e:
            print(f"Error: {getattr(e, 'user_message', e)}")
            sys.exit(1)
        print(format_criteria(proposed))
        if current and not args.yes:
            answer = input(f"\nOverwrite {path}? [y/N] ").strip().lower()
            if answer != "y":
                print("Not saved. Re-run with --yes to skip this prompt.")
                return
        save_criteria(proposed, path)
        print(f"Saved to {path}. Screening prompts, CSV columns and the web UI pick this up automatically.")

    elif args.command == "chat":
        if not os.path.exists(args.input):
            print(f"Error: Input file '{args.input}' not found. Run 'screen' (and ideally 'review') first.")
            sys.exit(1)
        from src.chat import ask, select_records
        from src.llm import LLMError
        with open(args.input, 'r') as f:
            data = json.load(f)
        records = data.get("records", [])
        results = data.get("screening_results", {})
        criteria = load_config().get("CRITERIA", {})
        n = len(select_records(records, results, args.scope))
        print(f"Chatting over {n} records (scope: {args.scope}). Type 'exit' to quit.")
        history = []

        def turn(question):
            history.append({"role": "user", "content": question})
            try:
                out = ask(history, records, results, criteria, scope=args.scope)
            except LLMError as e:
                history.pop()
                print(f"Error: {e.user_message}")
                return
            history.append({"role": "assistant", "content": out["reply"]})
            print(f"\n{out['reply']}\n")

        if args.ask:
            turn(args.ask)
            return
        try:
            while True:
                q = input("you> ").strip()
                if q.lower() in {"exit", "quit", "q"}:
                    break
                if q:
                    turn(q)
        except (KeyboardInterrupt, EOFError):
            print()

if __name__ == "__main__":
    main()
