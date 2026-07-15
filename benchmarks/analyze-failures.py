#!/usr/bin/env python3
"""Classify benchmark failures as retrieval (MemoryHub) or answering (LLM) errors.

Usage:
    python benchmarks/analyze-failures.py [results_file]

Requires port-forward to memoryhub-db:
    oc port-forward statefulset/memoryhub-pg 25432:5432 --context mcp-rhoai -n memoryhub-db
"""

import json
import sys
from collections import Counter
from pathlib import Path

import psycopg2

RESULTS_FILE = Path(
    sys.argv[1] if len(sys.argv) > 1
    else "benchmarks/amb-outputs/personamem/memoryhub/rag/32k.json"
)
DB_DSN = "postgresql://memoryhub:d64c86093e57f4e94aa4740974e70ad3@localhost:25432/memoryhub"


def classify_result(r: dict, cur) -> dict:
    """Classify a single query result. Returns a diagnosis dict."""
    ctx = r.get("context", "")
    ctx_tokens = r.get("context_tokens", 0)
    correct = r.get("correct", False)
    query = r.get("query", "")
    meta = r.get("meta", {})

    # Extract user name from query
    user_name = "unknown"
    if "User: " in query:
        user_name = query.split("User: ")[1].split("\n")[0].strip()

    # Count memories in context
    mem_count = ctx.count("## Memory")

    # Check for stubs vs full content
    stubs = 0
    full = 0
    if mem_count > 0:
        for m in ctx.split("## Memory")[1:]:
            if "[scope=project, weight=" in m and len(m.strip()) < 400:
                stubs += 1
            else:
                full += 1

    # Look up user's memories in DB
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE branch_type IS NULL) as parents,
               COUNT(*) FILTER (WHERE branch_type = 'chunk') as chunks
        FROM memory_nodes
        WHERE tenant_id = 'amb-benchmark' AND content LIKE %s
        AND branch_type IS NULL
    """, (f"%{user_name}%",))
    db_row = cur.fetchone()
    db_parents = db_row[0] if db_row else 0

    # Classify
    if correct:
        failure_mode = None
    elif mem_count == 0:
        failure_mode = "RETRIEVAL: no memories returned"
    elif stubs > 0 and full == 0:
        failure_mode = "RETRIEVAL: all stubs, no full content"
    elif stubs > full:
        failure_mode = "RETRIEVAL: mostly stubs"
    elif mem_count < db_parents and db_parents > 0:
        failure_mode = f"RETRIEVAL: incomplete ({mem_count}/{db_parents} parents)"
    elif ctx_tokens < 100 and db_parents > 0:
        failure_mode = "RETRIEVAL: context too short"
    else:
        failure_mode = "LLM: wrong answer with full context"

    return {
        "query_id": r.get("query_id", "?"),
        "user_name": user_name,
        "question_type": meta.get("question_type", "?"),
        "topic": meta.get("topic", "?"),
        "correct": correct,
        "expected": r.get("gold_answers", ["?"])[1] if len(r.get("gold_answers", [])) > 1 else "?",
        "got": r.get("answer", "?"),
        "mem_count": mem_count,
        "full_count": full,
        "stub_count": stubs,
        "ctx_tokens": ctx_tokens,
        "ctx_chars": len(ctx),
        "db_parents": db_parents,
        "retrieve_ms": r.get("retrieve_time_ms", 0),
        "failure_mode": failure_mode,
        "reasoning": r.get("reasoning", "")[:200],
    }


def main():
    if not RESULTS_FILE.exists():
        print(f"Results file not found: {RESULTS_FILE}")
        sys.exit(1)

    with open(RESULTS_FILE) as f:
        data = json.load(f)

    results = data.get("results", [])
    print(f"Total queries: {len(results)}")
    print(f"Correct: {sum(1 for r in results if r.get('correct'))}")
    print(f"Incorrect: {sum(1 for r in results if not r.get('correct'))}")
    print()

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    diagnoses = [classify_result(r, cur) for r in results]
    conn.close()

    failures = [d for d in diagnoses if d["failure_mode"] is not None]
    correct_count = sum(1 for d in diagnoses if d["correct"])

    # Summary by failure mode
    mode_counts = Counter(d["failure_mode"] for d in failures)
    retrieval_failures = sum(v for k, v in mode_counts.items() if k.startswith("RETRIEVAL"))
    llm_failures = sum(v for k, v in mode_counts.items() if k.startswith("LLM"))

    print("=" * 70)
    print("FAILURE CLASSIFICATION")
    print("=" * 70)
    print(f"  Retrieval (MemoryHub) failures: {retrieval_failures}")
    print(f"  Answering (LLM) failures:       {llm_failures}")
    print()
    for mode, count in mode_counts.most_common():
        print(f"  {count:3d}  {mode}")

    # Summary by question type
    print()
    print("=" * 70)
    print("BY QUESTION TYPE")
    print("=" * 70)
    type_stats: dict[str, dict] = {}
    for d in diagnoses:
        qt = d["question_type"]
        if qt not in type_stats:
            type_stats[qt] = {"total": 0, "correct": 0, "retrieval": 0, "llm": 0}
        type_stats[qt]["total"] += 1
        if d["correct"]:
            type_stats[qt]["correct"] += 1
        elif d["failure_mode"] and d["failure_mode"].startswith("RETRIEVAL"):
            type_stats[qt]["retrieval"] += 1
        else:
            type_stats[qt]["llm"] += 1

    for qt, stats in sorted(type_stats.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
        acc = stats["correct"] / stats["total"] * 100 if stats["total"] else 0
        print(f"  {qt:50s} {acc:5.1f}%  ({stats['correct']}/{stats['total']})  ret={stats['retrieval']} llm={stats['llm']}")

    # Summary by persona
    print()
    print("=" * 70)
    print("BY PERSONA (failures only)")
    print("=" * 70)
    persona_failures: dict[str, list] = {}
    for d in failures:
        persona_failures.setdefault(d["user_name"], []).append(d)
    for name, pf in sorted(persona_failures.items(), key=lambda x: -len(x[1])):
        modes = Counter(d["failure_mode"] for d in pf)
        mode_str = ", ".join(f"{v}x {k}" for k, v in modes.most_common())
        print(f"  {name:30s} {len(pf)} failures: {mode_str}")

    # Retrieval context stats
    print()
    print("=" * 70)
    print("CONTEXT DELIVERY STATS")
    print("=" * 70)
    ctx_tokens_list = [d["ctx_tokens"] for d in diagnoses]
    mem_counts = [d["mem_count"] for d in diagnoses]
    stub_counts = [d["stub_count"] for d in diagnoses]
    retrieve_times = [d["retrieve_ms"] for d in diagnoses]
    print(f"  Context tokens:  min={min(ctx_tokens_list):,}  max={max(ctx_tokens_list):,}  avg={sum(ctx_tokens_list)//len(ctx_tokens_list):,}")
    print(f"  Memories/query:  min={min(mem_counts)}  max={max(mem_counts)}  avg={sum(mem_counts)/len(mem_counts):.1f}")
    print(f"  Stub results:    {sum(stub_counts)} total across {len(diagnoses)} queries ({sum(1 for s in stub_counts if s > 0)} queries had stubs)")
    print(f"  Retrieve time:   min={min(retrieve_times):.0f}ms  max={max(retrieve_times):.0f}ms  avg={sum(retrieve_times)/len(retrieve_times):.0f}ms")

    # Detail on retrieval failures
    if retrieval_failures > 0:
        print()
        print("=" * 70)
        print("RETRIEVAL FAILURE DETAILS")
        print("=" * 70)
        for d in failures:
            if d["failure_mode"].startswith("RETRIEVAL"):
                print(f"  Q {d['query_id'][:8]}... [{d['user_name']}] {d['question_type']}")
                print(f"    {d['failure_mode']}")
                print(f"    Context: {d['mem_count']} memories, {d['ctx_tokens']} tokens, {d['stub_count']} stubs")
                print(f"    DB has: {d['db_parents']} parents for this user")
                print()

    # Detail on LLM failures (first 10)
    if llm_failures > 0:
        print()
        print("=" * 70)
        print(f"LLM FAILURE DETAILS (showing first 10 of {llm_failures})")
        print("=" * 70)
        llm_fails = [d for d in failures if d["failure_mode"].startswith("LLM")]
        for d in llm_fails[:10]:
            print(f"  Q {d['query_id'][:8]}... [{d['user_name']}] {d['question_type']}")
            print(f"    Expected: {d['expected']} | Got: {d['got']}")
            print(f"    Context: {d['mem_count']} memories, {d['ctx_tokens']:,} tokens")
            print(f"    Reasoning: {d['reasoning'][:150]}")
            print()

    # Final verdict
    print("=" * 70)
    accuracy = correct_count / len(diagnoses) * 100 if diagnoses else 0
    print(f"OVERALL: {accuracy:.1f}% accuracy ({correct_count}/{len(diagnoses)})")
    print(f"  MemoryHub retrieval: {'CLEAN' if retrieval_failures == 0 else f'{retrieval_failures} FAILURES'}")
    print(f"  LLM answering: {llm_failures} errors on {len(diagnoses)} queries")
    print("=" * 70)


if __name__ == "__main__":
    main()
