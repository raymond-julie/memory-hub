# D5 Readiness Audit — Assumed vs Real MemoryHub Capability

**Status:** Claim inventory drafted 2026-07-15; verification sweep not yet run
**Design refs:** `planning/platform-memory-benchmark.md` (Dimension 5 taxonomy
+ scoring design), `docs/benchmark-system-map.md` (methodology precedent),
CLAUDE.md "Verify Before Propagating"

## Why

Dimension 5 (downstream agent behavior) scores whether memory changes agent
behavior toward user intent. Its scoring protocol makes hard demands on
MemoryHub: retrieval attribution, injection records, contradiction machinery,
version-chain reads, SDK parity. Several of these are *claimed* in project
docs, agent rules, or design sketches but have never been verified against
the deployed server — and the project has now logged four incidents of
unverified capability claims propagating (chunking, PVC, sidecar, upstream
triage). This audit applies the lesson proactively: inventory every
capability D5 depends on, verify each against code/live state, and file the
gaps as product issues BEFORE D5 design work begins.

Rule for this document: no row gets a verdict without a cited artifact.
Every row below is UNVERIFIED until the sweep session fills it in.

## Claim inventory

Columns: capability / where it's claimed / what D5 needs it for /
verification method / verdict (REAL, PARTIAL, ASPIRATIONAL) + citation.

### A. Attribution and telemetry (the hard requirements)

| # | Capability | Claimed in | D5 needs it for | Verify by | Verdict |
|---|-----------|-----------|-----------------|-----------|---------|
| A1 | Per-session retrieval log: query -> returned memory IDs, timestamps | implied by "audit trails" in governance story | Memory-attribution check (correct behavior must trace to a retrieval) | Read audit-log schema + code: does it record READS or only writes? | UNVERIFIED |
| A2 | Injection record: what the SessionStart hook injected per session | `.claude/rules/memoryhub-loading.md` (hook injects `<memoryhub-context>`) | Distinguish "memory in context via injection" from "retrieved mid-session" | Trace hook path; is the injected working set recorded server-side? | UNVERIFIED |
| A3 | Session identity: `register_session` ties a run to a queryable session record | memoryhub-loading.md | Per-run traces; memoryless-vs-enabled pairing | Call it; inspect what's stored | UNVERIFIED |
| A4 | Version-chain read API via MCP (get history for a memory) | `MemoryPlatform` ABC sketch (`get_version_history`), versioning claims in CLAUDE.md/README | Temporal + correction tasks; churn verification | Enumerate MCP tool surface; is history actually exposed, or DB-only? | UNVERIFIED |

### B. Behavior machinery (correction / escalation classes)

| # | Capability | Claimed in | D5 needs it for | Verify by | Verdict |
|---|-----------|-----------|-----------------|-----------|---------|
| B1 | `report_contradiction` records contradictions with counts | memoryhub-loading.md | Correction tasks (agent surfaces conflict) | Call it; check schema + count increment | UNVERIFIED |
| B2 | Server "surfaces stale memories for review" from contradiction counts | memoryhub-loading.md — **likely ASPIRATIONAL: this is #353 (staleness sweep), not built** | Correction-task ground truth; also honesty of agent-facing docs | Grep for any surfacing mechanism; if absent, fix the rules doc | UNVERIFIED |
| B3 | `branch_type="rationale"` branches via `parent_id` | memoryhub-loading.md; used by MH-FIRST memory write | Policy memories with load-bearing "why" (escalation tasks) | Write one; read it back; check search behavior | UNVERIFIED |
| B4 | Weight semantics: weight actually affects injection/ranking (1.0 policies surface reliably) | memoryhub-loading.md ("set weights deliberately"); `generate_stub` docstring ("injection weight") | Escalation tasks depend on policy memories reliably reaching context | Trace weight through search ranking + working-set selection; test 1.0-vs-0.5 surfacing | UNVERIFIED |
| B5 | Scope isolation: user/project/organizational/enterprise all enforced end-to-end | memoryhub-loading.md; RBAC claims | Task isolation; multi-principal D5 scenarios | Existing tests? Cross-scope read attempt | UNVERIFIED |

### C. SDK / MCP surface parity (the ambiguity Wes observed)

| # | Capability | Claimed in | D5 needs it for | Verify by | Verdict |
|---|-----------|-----------|-----------------|-----------|---------|
| C1 | Released SDK parity with server surface (disabled_signals, tenant_id) | assumed by harness; **known-lagging: PyPI 0.14.0 lacks both** | D5 harness drives agents through the SDK; every lag is a blocker | Diff released-SDK surface vs MCP tool schema; publish parity matrix; define release cadence | **PARTIAL** -- characterized 2026-07-14. PyPI 0.14.0 `search()` lacks `disabled_signals` (added `d7dd3f0` #354) and `tenant_id` (added `42be4ed` #368). Server supports both. Adapter Containerfile bundles local SDK as workaround. Customers on PyPI cannot use these server capabilities. C2 merged here: the "SDK-to-MCP send limitation" IS this release-parity gap, not a separate structural issue. Filed as #381. |
| C3 | Per-request tenant selection Phase 2 (`authorized_tenants` in JWT) | #368 Phase 1 shipped own-tenant-only; Phase 2 is a hook, not an implementation | Multi-tenant D5 scenarios; benchmark service accounts | Read `resolve_tenant()`; confirm Phase 2 status | UNVERIFIED (known partial) |
| C4 | MCP search response content fidelity (full content vs stubs/truncation) | assumed by all benchmark runs; **H6 in the theory doc suspects lossiness** | All classes — behavior depends on what actually reaches context | The H6 context-delivery audit (already planned in Matrix A session) | UNVERIFIED |

### D. Baseline and control (harness-side, listed for completeness)

| # | Capability | Needed for | Note |
|---|-----------|-----------|------|
| D1 | Memoryless baseline mode (same agent, no memory) | Delta-scoring | Harness-side: skip registration / empty tenant. Verify no hidden fallback injection. |
| D2 | Controlled memory pre-load (seed exact memories for a task) | Task construction | write_memory suffices? Verify determinism (embedding versioning). |
| D3 | Run reproducibility: pin memory-state snapshot per task | pass^k repeated trials | Needs corpus snapshot/restore per trial — relates to #348 rollback machinery. |

## Sweep session (file via /issue-tracker when ready)

`benchmarking: D5 readiness audit — verify assumed MemoryHub capabilities` (#382)

- **Session scope:** read/trace/verify every UNVERIFIED row above; fill
  verdicts with citations; correct any agent-facing doc making
  aspirational claims (B2 is the known suspect — memoryhub-loading.md
  must not promise what #353 hasn't built); file one MH issue per
  confirmed gap (MemoryHub-first: these are product gaps found early,
  same as tenant_id/#368).
- **Exit predicate:** zero UNVERIFIED rows; every verdict cited; agent-
  facing docs match verified reality; gap issues filed and linked here.
- **Verifier:** each verdict has a code path, live call output, or test.
- **Not blocking:** Matrix A or Phases 5-7. Natural slot: filler /
  parallel-ok, but MUST complete before #337's D5 implementation starts.
  Note overlaps: A1 relates to #347's decision log; B2 to #353; D3 to
  #348; C4 rides the already-planned H6 audit.
- **Circuit breaker:** timebox one session; rows still unverified at
  the end stay UNVERIFIED in the doc (honest) with a follow-up filed —
  do not guess verdicts to hit the exit predicate.

## Companion sweep: agent-facing docs (separate issue, cheaper, do first)

`docs: Capability-claim sweep of agent-facing documents — provable-only` (#383)

**Principle (decided 2026-07-15):** documents are swept by *audience*, not
by a blanket provability rule. Agent-facing docs are executed, not read —
an aspirational claim there is a bug (the agent behaves as if the
capability exists). Planning docs keep their aspirations; that is their
function. `docs/` holds shipped reality per the existing CLAUDE.md
convention. Mixed-audience docs (README) mark every capability claim
Shipped / Partial (+issue) / Planned (+issue).

- **Session scope (loop-shaped):** enumerate agent-facing surfaces —
  `.claude/rules/memoryhub-loading.md`, CLAUDE.md capability statements,
  `memory-hub-mcp` SYSTEM_PROMPT.md and tool descriptions/docstrings,
  SDK docstrings. For each capability claim: verify against code/live
  state; keep (with citation), demote to planning/ + backlog issue link,
  or rewrite to match reality. B2 (contradiction "surfacing") is the
  known first offender.
- **Exit predicate:** every capability claim in agent-facing surfaces is
  either verified or removed/rewritten; demotions link to a tracking
  issue; a one-line placement rule proposed for CLAUDE.md (via the
  dedicated-PR approval process): "Agent-facing docs state only verified
  capabilities; intended features live in planning/ with an issue link."
- **Verifier:** grep sweep list with per-claim verdict; second pass
  returns zero unmarked claims.
- **Circuit breaker:** one session; unfinished surfaces listed at the
  end of this doc, not silently skipped.

## Sequencing position (answers "features before benchmarking?")

Neither-first — the interleave already in the epic plan, with the ratio
rebalanced: Matrix A closes the retrieval question, then Phase 5
(reconciliation/rollback) becomes the priority. Phase 5-7 work closes
most D5 gaps as a side effect (A1~#347 decision log, B2=#353, D3=#348),
so feature development IS the D5 unblocking path. The benchmark tiers
that need features (Matrix B, D5) are already sequenced after them.
Benchmarking's record this epic: found #368, disabled_signals, SDK
parity, and one production defect (chunk recall) — it orders feature
work by demonstrated need; it does not compete with it.
