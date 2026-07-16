# Session Summary -- 2026-07-15 -- dreaming -- Chunk sweep + fact extraction prototype

**Plan:** NEXT_SESSION-dreaming.md (chunk sweep + tuning)
**Commits:** d398b33..40dd0a0 (`feat/chunk-params-sweep`)
**Deployed:** MCP server (3 deploys: chunk params, authz fix, max_results cap)
**Model:** Opus 4.6 (1M context)

## Plan vs. actual

Planned: Run 12-config chunk sweep (4 sizes x 3 overlaps) to find
optimal chunk parameters, then pick the best config. Shipped: 21-config
sweep (7 sizes x 3 overlaps, extended to 32/64/128 tokens), plus a
Mem0-style fact extraction prototype that tests a fundamentally different
retrieval unit. The fact extraction work was unplanned but emerged from
the sweep's key finding: chunk size doesn't matter for PersonaMem.
Scope expanded significantly but productively.

## Shipped

- `d398b33` Per-request chunk params: overlap_tokens in chunker, chunk_target_tokens/chunk_overlap_tokens threaded through write API (schema, service, MCP tool, dispatcher, SDK)
- `84e2383` authorize_write cross-tenant fix: honour authorized_tenants list (was blocking sweep re-ingestion)
- `4a0d0c4` max_results cap raised from 50 to 200 (facts need higher k)
- `79537e1` MEMORYHUB_K env var fix: async_retrieve was ignoring it (hardcoded k=10 in base class default)
- `1a42afd` Sweep runner script with resume/skip logic
- `40dd0a0` Fact extraction script (extract_facts.py)
- Chunk sweep: 7 complete 589-query runs, several partials
- Fact extraction: 6,697 facts from 195 docs, full benchmark at k=70

## Verification & confidence

- Chunk sweep: 7 complete configs (c32-o0, c32-o25, c256-o0/o10/o25, c512-o10/o25) all 589 queries each, run against live MCP server with Gemini Flash Lite. All converge to 62.0-62.6%.
- Facts-lite-k70: 589 queries, 63.3% accuracy, 1,256 avg context tokens. Run against same live infrastructure.
- Chunker overlap: 16 unit tests passing, including overlap-specific edge cases.
- Cross-tenant authz fix: verified via mcp-test-mcp direct tool calls + full ingestion runs.
- Confidence: **high** on the chunk-size-doesn't-matter finding (7 independent runs converge). **medium** on the facts result (single extraction model, single k value, single run).

## Judgment calls & deviations

- Extended sweep from 4 to 7 chunk sizes (added 32, 64, 128) based on early data showing smaller-is-no-worse. This was the right call: confirmed the flat accuracy curve extends down to 32 tokens.
- Pivoted to fact extraction mid-session when sweep data showed chunk tuning is a dead end. The fact extraction prototype was unplanned but directly validated by the sweep findings.
- Used Gemini Flash Lite for extraction (cheapest option) per user preference. Higher quality extraction models untested.
- Raised max_results from 50 to 200 as a product change, not just benchmark scaffolding. Customers with many small memories need higher k.

## Key findings

**Chunk size is irrelevant for PersonaMem.** Every complete run from 32 to 512 tokens lands at 62.0-62.6%. Overlap (0/10/25%) makes no measurable difference. The retrieval pipeline finds the right neighborhood regardless of chunk granularity.

**The 8.5pp gap to parents-mode (70.8%) is not about retrieval unit.** Both chunks and facts land at ~62-63% with ~1,200-1,600 context tokens. Parents-mode provides ~28K tokens of full document context. The gap is model capability: Flash Lite synthesizes better from full documents than from focused fragments.

**Fact extraction matches or beats chunks.** facts-lite-k70: 63.3% (best overall) with 21% less context than best chunk config. Per-category: +15.8pp on generalization, +8.1pp on recalling reasons, but -5.4pp on recall and -7.6pp on suggest_new_ideas.

**max_results chain is a silent funnel.** The pipeline has 4 capping points (base class default, provider env var, SDK config, server tool). The effective k was silently 10 despite MEMORYHUB_K=70 because the base class default preempted everything. Fixed, but the chain needs observability (filed mentally, issue TBD).

## Backlog delta

Filed: none formally. Deferred: retrieval chain observability issue (max_results funnel telemetry), GPU machineset scale-back (still at 3 nodes).

## Drift & forward-collisions

- Backward: #343 (chunk tuning) -- sweep data proves chunk size tuning is a dead end for PersonaMem. The issue's premise ("tune chunk size to improve accuracy") is invalidated. Recommend re-scoping to "ship per-request chunk params as a product feature" and closing the tuning aspect.
- Forward: #347 (dreaming/reconciliation) -- fact extraction at write time is essentially eager dreaming. The extract_facts.py prototype validates the approach; the production implementation should use FastMCP sampling per the design discussion.

## For the reviewer

- Sanity-check: The flat accuracy curve across chunk sizes is surprising and counterintuitive. Worth validating on a second dataset (LoCoMo or LongMemEval) to confirm it's not PersonaMem-specific.
- Thin verification: The fact extraction run is a single run with a single extraction model. The 63.3% vs 62.6% delta (+0.7pp) is within noise. Need multiple runs or a different extraction model to confirm facts genuinely beat chunks.
- Wants guidance: Should we invest in write-time fact extraction as a product feature (via FastMCP sampling), or is the marginal gain over chunks too small to justify the added LLM call per write?

## Risks / watch-fors

- GPU machineset still at 3 nodes (scaled from 2 for embedding GPU). Scale back when benchmarking is done.
- The k=10 bug means ALL previous benchmark runs with MEMORYHUB_K>10 were actually running at k=10. The 70.8% parents-mode baseline was NOT affected (it uses k=70 via the sync retrieve path, which did read the env var). But any chunks-mode run that set MEMORYHUB_K>10 was silently capped.
- Many partial sweep configs exist on disk (c512-o0 at 510q, c1024-o0 at 210q, etc.). These are not reliable results. Only use the 589-query complete runs.
- The sweep created 15+ projects in the database (amb-c32-o0-k10, etc.). These should be cleaned up after the analysis is finalized.
