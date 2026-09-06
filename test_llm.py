"""Smoke-test the LLM connection: list the models your key can use and run one
tiny JSON-mode completion with each configured task model.

Works with either provider (LLM_PROVIDER=orcarouter | gemini in .env, or
auto-detected from ORCA_API_KEY / GEMINI_API_KEY).

    python3 test_llm.py                     # list models + test configured model(s)
    python3 test_llm.py --models            # only list models
    python3 test_llm.py --provider gemini   # force a provider for this run
"""
import os
import sys


def main():
    print("[deprecated] test_llm.py is deprecated and will be removed in a future release; use "
          "Settings -> Test connection / Load model list in the web interface instead.", file=sys.stderr)
    argv = sys.argv[1:]
    if "--provider" in argv:
        idx = argv.index("--provider")
        if idx + 1 >= len(argv):
            print("Usage: python3 test_llm.py [--models] [--provider orcarouter|gemini]")
            return 2
        os.environ["LLM_PROVIDER"] = argv[idx + 1]

    # Imported after the env override so load_config() sees it.
    from src.config import load_config
    from src.llm import (LLMClient, LLMError, model_for, parse_json_text, provider_settings,
                         screening_model, ScreeningModelError, TASKS)

    try:
        config = load_config()
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    ps = provider_settings(config)
    print(f"--- {ps['label']} API Test Script ---")
    if not ps["api_key"]:
        print(f"Error: {ps['key_env']} not found in .env file.")
        print(f"Get a key at {ps['console']} (see .env.example).")
        return 1

    client = LLMClient(ps["api_key"], ps["base_url"], provider=ps["provider"])
    print(f"Provider: {ps['label']}  Base URL: {client.base_url}\n")

    print("1. Models available to this key:")
    try:
        models = client.list_models()
        for m in models:
            mid = m.get("id", "?")
            ctx = m.get("context_length")
            pricing = m.get("pricing") or {}
            price = ""
            if pricing.get("prompt_per_million"):
                price = f"${pricing['prompt_per_million']}/M in, ${pricing.get('completion_per_million', '?')}/M out"
            print(f"   - {mid:<45} {('ctx ' + str(ctx)) if ctx else '':<14} {price}")
        print(f"   ({len(models)} models)\n")
    except LLMError as e:
        print(f"   Error listing models: {e}\n")

    if "--models" in argv:
        return 0

    print("2. Testing the configured model for each task:")
    try:
        screening_model(config)
    except ScreeningModelError as e:
        print(f"   [!] screening: {e}\n")
    tested = {}
    for task in TASKS:
        model = model_for(task, config)
        if model in tested:
            print(f"   {task:<10} {model:<35} (same as above)")
            continue
        try:
            resp = client.chat(
                [{"role": "user", "content": 'Reply with the JSON object {"ok": true, "model": "<your model name>"}.'}],
                model=model, json_mode=True,
            )
            parsed = parse_json_text(resp.text)
            usage = resp.usage or {}
            routed = f" -> routed to {resp.model_label}" if resp.resolved_model else ""
            cache = f", cache {'HIT' if resp.cached else 'MISS'}" if ps["provider"] == "orcarouter" else ""
            print(f"   {task:<10} {model:<35} OK{routed} "
                  f"({usage.get('total_tokens', '?')} tokens{cache})")
            tested[model] = parsed
        except LLMError as e:
            print(f"   {task:<10} {model:<35} FAILED: {e}")
            if ps["provider"] == "orcarouter" and e.status == 402:
                print("              -> add credits at https://www.orcarouter.ai/console/billing, "
                      "or use free models: MODEL_NAME=orcarouter/free and, for screening, a "
                      "concrete '-free' id from the list above (e.g. "
                      "MODEL_SCREENING=deepseek/deepseek-v4-flash-free)")
            elif ps["provider"] == "gemini" and e.status == 404:
                print("              -> this Gemini model is not available to your key; pick one "
                      "from the list above (drop the 'models/' prefix) and set MODEL_NAME")
            tested[model] = None
        except Exception as e:  # noqa: BLE001
            print(f"   {task:<10} {model:<35} FAILED (bad JSON): {e}")
            tested[model] = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
