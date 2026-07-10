#!/usr/bin/env python3
"""Run the retrieval-at-scale benchmark (#271).

Generates synthetic corpora at 100/1K/10K scale, seeds into PostgreSQL+pgvector,
and measures search latency and relevance. Results are written to benchmarks/.

Requires a running PostgreSQL+pgvector instance. Configure via env vars:
    MEMORYHUB_DB_HOST (default: localhost)
    MEMORYHUB_DB_PORT (default: 15433)
    MEMORYHUB_DB_NAME (default: memoryhub_bench)
    MEMORYHUB_DB_USER (default: memoryhub)
    MEMORYHUB_DB_PASS (default: memoryhub)

Usage:
    python scripts/bench-retrieval-scale.py
    python scripts/bench-retrieval-scale.py --scales 100 1000
    python scripts/bench-retrieval-scale.py --runs 5
"""

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.perf.retrieval_scale_bench import run_scale_benchmark


def main():
    parser = argparse.ArgumentParser(description="Retrieval-at-scale benchmark (#271)")
    parser.add_argument(
        "--scales", type=int, nargs="+", default=[100, 1000, 10000],
        help="Corpus sizes to benchmark (default: 100 1000 10000)",
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Runs per scale tier for stability (default: 3)",
    )
    parser.add_argument("--db-url", type=str, default=None, help="Database URL override")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = asyncio.run(
        run_scale_benchmark(
            scales=args.scales,
            runs_per_scale=args.runs,
            db_url=args.db_url,
        )
    )

    # Print summary table
    print("\n" + "=" * 80)
    print("RETRIEVAL-AT-SCALE BENCHMARK RESULTS")
    print("=" * 80)
    print(f"{'Scale':>8} {'p50ms':>8} {'p95ms':>8} {'p99ms':>8} "
          f"{'R@10':>8} {'P@10':>8} {'MRR':>8} "
          f"{'kwR@10':>8} {'kwP@10':>8} {'kwMRR':>8}")
    print("-" * 80)
    for tier in result.tiers:
        print(f"{tier.scale:>8} {tier.latency_p50_ms:>8.1f} {tier.latency_p95_ms:>8.1f} "
              f"{tier.latency_p99_ms:>8.1f} {tier.recall_at_10:>8.3f} "
              f"{tier.precision_at_10:>8.3f} {tier.mrr_score:>8.3f} "
              f"{tier.keyword_recall_at_10:>8.3f} {tier.keyword_precision_at_10:>8.3f} "
              f"{tier.keyword_mrr_score:>8.3f}")
    print(f"\nTotal time: {result.total_seconds:.1f}s")

    # Write results JSON
    benchmarks_dir = Path(__file__).resolve().parent.parent / "benchmarks"
    benchmarks_dir.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    out_path = benchmarks_dir / f"retrieval-scale-{date_str}.json"

    result_dict = dataclasses.asdict(result)
    with open(out_path, "w") as f:
        json.dump(result_dict, f, indent=2)
    print(f"\nResults written to: {out_path}")


if __name__ == "__main__":
    main()
