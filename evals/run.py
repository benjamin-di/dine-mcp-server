"""Run the evaluation tasks with a real model against the synthetic world, and record the results.

    uv run python evals/run.py --model <configured-model> [--runs 3] [--held-out] [--only ID ...]

Each task runs in a fresh `claude -p` with only this server's tools (stdio, evals/fixture_server.py)
and is checked by the regular expressions in tasks.toml. Results (success per run, turns, tokens,
cost) go to evals/results/<timestamp>.json and a summary is printed. Requires the Claude Code CLI.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
ROOT = HERE.parent


def mcp_config(state: str) -> str:
    config = {
        "mcpServers": {
            "dine": {
                "command": sys.executable,
                "args": [str(HERE / "fixture_server.py")],
                "env": {"DINE_EVAL_STATE": state, "LOG_LEVEL": "WARNING"},
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
    return handle.name


def run_once(task: dict, model: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="dine-eval-") as state:
        config = mcp_config(state)
        try:
            return _run_model(task, model, config)
        finally:
            Path(config).unlink(missing_ok=True)


def _run_model(task: dict, model: str, config: str) -> dict:
    now = datetime.now(ZoneInfo("Asia/Singapore"))
    system = (
        f"Today is {now:%A %d %B %Y}. The user is in Singapore unless they say otherwise. "
        "Use the dine tools to answer; you cannot browse the web."
    )
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                task["prompt"],
                "--model",
                model,
                "--mcp-config",
                config,
                "--strict-mcp-config",
                "--allowedTools",
                "mcp__dine__dine_find_venue,mcp__dine__dine_get_venue,mcp__dine__dine_check_availability,mcp__dine__dine_status",
                "--disallowedTools",
                "Bash,Read,Write,Edit,WebFetch,WebSearch,Task,Agent",
                "--append-system-prompt",
                system,
                "--output-format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=tempfile.gettempdir(),
        )
    except subprocess.TimeoutExpired:
        return {"id": task["id"], "ok": False, "error": "model_timeout", "seconds": time.monotonic() - started}
    elapsed = time.monotonic() - started
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return {"id": task["id"], "ok": False, "error": (proc.stderr or proc.stdout)[-300:], "seconds": elapsed}
    if not isinstance(out, dict) or not isinstance(out.get("result", ""), str):
        return {"id": task["id"], "ok": False, "error": "invalid_model_response", "seconds": elapsed}
    answer = out.get("result") or ""
    missing = [p for p in task.get("expect", []) if not re.search(p, answer, re.I | re.S)]
    present = [p for p in task.get("forbid", []) if re.search(p, answer, re.I | re.S)]
    usage = out.get("usage") or {}
    return {
        "id": task["id"],
        "ok": proc.returncode == 0 and not missing and not present and not out.get("is_error"),
        "missing": missing,
        "forbidden": present,
        "turns": out.get("num_turns"),
        "input_tokens": usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": out.get("total_cost_usd"),
        "seconds": round(elapsed, 1),
        "answer": answer[:1500],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Model selected by your environment; no built-in model default")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--held-out", action="store_true", help="run the held-out tasks instead")
    parser.add_argument("--all", action="store_true", help="run every task")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.runs < 1 or args.jobs < 1:
        parser.error("--runs and --jobs must be positive")
    tasks = tomllib.loads((HERE / "tasks.toml").read_text())["task"]
    if args.only and set(args.only) - {task["id"] for task in tasks}:
        parser.error("--only contains unknown task ids")
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    elif not args.all:
        tasks = [t for t in tasks if bool(t.get("held_out")) == args.held_out]
    if not tasks:
        parser.error("No evaluation tasks selected")
    jobs = [(task, run) for task in tasks for run in range(args.runs)]
    results = []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for result in pool.map(lambda job: run_once(job[0], args.model), jobs):
            results.append(result)
            mark = "ok " if result["ok"] else "FAIL"
            print(f"{mark} {result['id']:<24} turns={result.get('turns')} {result.get('seconds')}s", flush=True)
            if not result["ok"]:
                print(
                    "     missing:",
                    result.get("missing"),
                    "forbidden:",
                    result.get("forbidden"),
                    result.get("error", ""),
                )
    passed = sum(r["ok"] for r in results)
    summary = {
        "model": args.model,
        "at": datetime.now().isoformat(timespec="seconds"),
        "set": "all" if args.all else "held_out" if args.held_out else "dev",
        "runs": len(results),
        "success_rate": round(passed / max(1, len(results)), 3),
        "mean_turns": round(sum(r.get("turns") or 0 for r in results) / max(1, len(results)), 2),
        "mean_input_tokens": int(sum(r.get("input_tokens") or 0 for r in results) / max(1, len(results))),
        "results": results,
    }
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{summary['at'].replace(':', '')}-{summary['set']}.json"
    path.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    print(
        f"\n{passed}/{len(results)} passed ({summary['success_rate']:.0%}); mean turns {summary['mean_turns']}; {path}"
    )

    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
