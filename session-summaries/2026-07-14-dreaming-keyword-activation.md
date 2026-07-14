# Session Summary -- 2026-07-14 - dreaming - Keyword signal activation in non-focus path

**Plan:** NEXT_SESSION-dreaming.md / #372   **Commits:** 5d18879..2bf4ae9 (feat/keyword-signal-non-focus)
**Deployed:** dev   **Model:** Opus 4.6 (1M)

## Plan vs. actual
Planned: wire keyword/BM25 recall into the non-focus search_memories() path, corpus reset, 20-query smoke showing keyword on/off delta. Shipped: code wiring + deploy + corpus reset + smoke. Slipped: smoke delta is zero (corpus too small, not a code issue).
Scope: expanded to include benchmark harness MEMORYHUB_TENANT_ID support and amb-benchmark ConfigMap user (required for cross-tenant smoke testing).

## Shipped
- `5d18879` retrieval: keyword recall in search_memories() with RRF blend (cosine + keyword), disabled_signals threading, unit + integration tests
- `2bf4ae9` bench: MEMORYHUB_TENANT_ID env var in harness provider, keyword on/off smoke configs
- Corpus reset: 6,419 chunks deleted, verified 195 parents / 0 chunks
- ConfigMap: amb-benchmark user added with tenant_id=amb-benchmark for benchmark isolation
- Filed #378 (disabled_signals silently ignored on non-focus path), fixed in same PR
- PR #379 open, closes #372 + #378

## Verification & confidence
- Unit tests: 19 passed (5 new keyword + 14 existing disabled_signals)
- Deployed to cluster, verified keyword code running in container (grep confirmed 6 occurrences of keyword_boost_weight in deployed memory.py)
- Smoke test: 55.0% accuracy keyword-on, 55.0% keyword-off. Zero delta (identical contexts).
- Confidence: **medium** on code correctness (unit tests pass, code deployed, keyword recall activates), **low** on signal value (cannot demonstrate differentiation on this corpus size)

## Judgment calls & deviations
- Corpus reset via scripted chunk DELETE rather than full re-ingest (safer, preserves parents)
- Created amb-benchmark ConfigMap user rather than moving data to default tenant (preserves multi-tenant isolation, which is the product's value prop)
- Applied MemoryHub-first litmus: filed #378 for silent disabled_signals drop rather than just fixing it silently
- Smoke shows no delta: root cause is ~5 docs/user in PersonaMem, not code issue. With k_recall=24 and max_results=10, cosine already captures every per-user document. Keyword can't surface new candidates from an already-exhausted pool.

## Backlog delta
Filed #378 (disabled_signals silent ignore, fixed in #379). PR #379 closes #372 + #378. Memory: feedback-memoryhub-first-benchmark. Deferred: smoke differentiation validation to post-chunking (#343) or larger corpus.

## Drift & forward-collisions
- Backward: #360 (Matrix A) still valid but now depends on #372 landing first (which it will via #379). The "all identical results" finding from the first matrix run is now fully explained: vector-only path + chunk contamination.
- Backward: #371 (system map + preflight) unaffected, still blocked by #369 (closed).
- Forward: #343 (chunking fix) will increase per-user node counts, which is exactly what keyword recall needs to differentiate. Comment proposed on #343.

## For the reviewer
- Sanity-check: the RRF blend in search_memories() uses 2 signals (cosine + keyword) while the focused path uses 5. Is the weight carving (weight_q = 1 - weight_k) correct for the 2-signal case, or should it match the focused path's proportional carving pattern?
- Thin verification: keyword differentiation is unproven on any real corpus. The code is verified to activate, but we have no evidence it improves retrieval quality. Integration tests scaffold exists but requires podman-compose stack.
- Wants guidance: should #372 close with the code shipped (exit predicate amended), or stay open until a larger corpus shows the delta?

## Risks / watch-fors
- PyPI SDK 0.14.0 does not have disabled_signals. Benchmark harness requires local SDK install. An SDK release would fix this for both the harness and customers.
- The amb-benchmark API key is in the ConfigMap (not a secret). Fine for dev; would need to move to a Secret for any production benchmark setup.
- CI Secret Scanning and Tests workflows both fail on main (pre-existing, not from this session).
