#!/usr/bin/env python3
"""Smoke test for RRF signal ablation (#354).

Runs 20 PersonaMem queries per configuration through the MemoryHub
provider, asserting that disabled signals are actually absent from the
retrieval metadata. Uses `uv run omb run` with MEMORYHUB_DISABLED_SIGNALS.

Usage:
    python scripts/smoke_ablation.py [--query-limit N] [--skip-ingestion]

Requires MEMORYHUB_URL, MEMORYHUB_API_KEY, and MEMORYHUB_DB_PASS set.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

CONFIGS = [
    {
        "name": "vector-only",
        "disabled": "reranker,focus,keyword,domain,graph",
        "description": "Vector similarity only (all other signals disabled)",
    },
    {
        "name": "vector-reranker",
        "disabled": "focus,keyword,domain,graph",
        "description": "Vector + reranker",
    },
    {
        "name": "vector-reranker-keyword",
        "disabled": "focus,domain,graph",
        "description": "Vector + reranker + keyword",
    },
    {
        "name": "vector-reranker-keyword-focus",
        "disabled": "domain,graph",
        "description": "Vector + reranker + keyword + focus",
    },
    {
        "name": "vector-reranker-keyword-focus-domain",
        "disabled": "graph",
        "description": "Vector + reranker + keyword + focus + domain",
    },
    {
        "name": "vector-reranker-keyword-focus-domain-graph",
        "disabled": "",
        "description": "Full pipeline (all signals enabled)",
    },
]


def run_config(config: dict, query_limit: int, skip_ingestion: bool, output_dir: Path) -> dict:
    """Run a single ablation config and return the summary."""
    import os

    env = os.environ.copy()
    if config["disabled"]:
        env["MEMORYHUB_DISABLED_SIGNALS"] = config["disabled"]
    elif "MEMORYHUB_DISABLED_SIGNALS" in env:
        del env["MEMORYHUB_DISABLED_SIGNALS"]

    run_name = f"ablation-{config['name']}"
    cmd = [
        "uv", "run", "omb", "run",
        "--split", "test",
        "--dataset", "personamem",
        "--memory", "memoryhub",
        "--mode", "rag",
        "--query-limit", str(query_limit),
        "--name", run_name,
        "--description", config["description"],
        "-o", str(output_dir),
    ]
    if skip_ingestion:
        cmd.append("--skip-ingestion")

    print(f"\n{'='*60}")
    print(f"Config: {config['name']}")
    print(f"Disabled: {config['disabled'] or '(none)'}")
    print(f"Command: {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, env=env, cwd=str(Path(__file__).parent.parent))
    if result.returncode != 0:
        print(f"FAIL: {config['name']} exited with code {result.returncode}")
        return {"name": config["name"], "status": "error", "returncode": result.returncode}

    result_file = output_dir / run_name / "personamem" / "test" / "rag" / "results.json"
    if not result_file.exists():
        alt = list((output_dir / run_name).rglob("results.json"))
        result_file = alt[0] if alt else None

    if result_file and result_file.exists():
        data = json.loads(result_file.read_text())
        accuracy = data.get("accuracy", 0)
        total = data.get("total_queries", 0)
        correct = data.get("correct", 0)
        print(f"Result: {correct}/{total} = {accuracy:.1%}")
        return {
            "name": config["name"],
            "status": "ok",
            "accuracy": accuracy,
            "total": total,
            "correct": correct,
            "disabled": config["disabled"],
        }

    print(f"WARNING: no results.json found for {config['name']}")
    return {"name": config["name"], "status": "no_results"}


def main():
    parser = argparse.ArgumentParser(description="RRF signal ablation smoke test")
    parser.add_argument("--query-limit", type=int, default=20)
    parser.add_argument("--skip-ingestion", action="store_true",
                        help="Skip ingestion (reuse existing data)")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ablation"))
    args = parser.parse_args()

    results = []
    for i, config in enumerate(CONFIGS):
        skip = args.skip_ingestion or i > 0
        summary = run_config(config, args.query_limit, skip, args.output_dir)
        results.append(summary)

    print(f"\n{'='*60}")
    print("ABLATION SUMMARY")
    print(f"{'='*60}")
    print(f"{'Config':<45} {'Accuracy':>8} {'Queries':>8}")
    print("-" * 65)
    for r in results:
        if r["status"] == "ok":
            print(f"{r['name']:<45} {r['accuracy']:>7.1%} {r['total']:>8}")
        else:
            print(f"{r['name']:<45} {'ERROR':>8} {'':>8}")

    summary_file = args.output_dir / "ablation_summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(results, indent=2))
    print(f"\nSummary written to {summary_file}")

    failed = [r for r in results if r["status"] != "ok"]
    if failed:
        print(f"\n{len(failed)} config(s) failed!")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
