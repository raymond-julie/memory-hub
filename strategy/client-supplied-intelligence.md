# Client-Supplied Intelligence: Extraction Without Server-Side Models

**Date:** 2026-07-16
**Status:** Positioning note (technical basis: `planning/eager-fact-extraction.md`)

## The one-sentence version

MemoryHub performs LLM-powered memory extraction using the *calling
agent's own model* via MCP sampling — the server needs no model
credentials, no GPU inference capacity, and no egress to a model
provider to turn raw content into structured, reconcilable memories.

## Why this matters to our customers

Memory products that do LLM extraction require the memory layer itself
to be provisioned with model access — a provider API key or a
co-deployed inference stack (verified 2026-07-16 against primary
sources): Hindsight's server takes `HINDSIGHT_API_LLM_API_KEY` /
`HINDSIGHT_API_LLM_PROVIDER` as deployment configuration and its README
states the retain operation "uses an LLM to extract key facts"
(github.com/vectorize-io/hindsight); Mem0's memory layer makes its own
LLM calls with `OPENAI_API_KEY` as the default, or a locally-hosted
endpoint such as Ollama (docs.mem0.ai). To be precise and fair: these
products DO support local/self-hosted models, so restricted environments
are not impossible for them — but the memory system remains a second
model surface to provision, govern, and pay for. With MCP sampling,
MemoryHub's extraction requires none of that: no key, no co-deployed
inference, no second surface. For our customer
base that is not a detail — it is often the blocking question:

1. **Air-gapped and restricted-egress environments.** A memory server
   that must call out to a model provider is a non-starter in
   disconnected deployments. With sampling, the extraction call rides
   the already-established client connection — no new egress path, no
   new firewall conversation.
2. **Model governance stays where it already lives.** Enterprises
   approve models per-team, per-workload, through existing processes.
   Sampling means extraction runs under the *caller's* approved model —
   whatever the agent is already using (a MaaS endpoint, a local vLLM,
   a hosted frontier model). MemoryHub never introduces a second model
   to govern, and never makes a model choice on the customer's behalf.
   (This also composes with early customer preferences on model
   selection: the customer's choice IS the extraction model.)
3. **Cost attribution is native.** Extraction cost lands on the caller
   who wrote the memory, on their existing model billing — not pooled
   into a platform bill nobody can allocate. Chargeback works without
   building chargeback.
4. **FIPS posture stays clean.** No bundled inference runtime on the
   server side means no additional crypto-validation surface for
   extraction. The server remains what it is: UBI-based services,
   PostgreSQL, MinIO, Valkey.
5. **Graceful capability ladder.** Clients that support MCP sampling
   get eager extraction (facts appear seconds after write). Clients
   that don't fall back to the server's background dreaming pipeline —
   which CAN use a server-configured model where the customer wants
   one. Both entry points feed the same governed pipeline
   (reconciliation, provenance, versioning, rollback).

## The demo sentence

"Write a meeting transcript into MemoryHub from Claude Code, and watch
it come back as queryable facts — extracted by *your* model, on *your*
bill, with the server never having touched an LLM." (Scope honestly when
asked: this is the eager/sampling path; the background fallback path can
use a server-configured model where the customer wants one.)

## Evidence to cite (as of 2026-07-16)

- Fact-based retrieval: 63.3% PersonaMem at 1,256 context tokens vs
  70.8% at ~28,000 tokens for full-context stuffing — 22x token
  efficiency for 7.5 points, and the best score of any budgeted mode
  (`benchmarks/results/`, 2026-07-15 sweep).
- Category signature: facts win the personalization-synthesis
  categories (+15.8pp generalizing to new scenarios, +8.1pp recalling
  reasons behind updates) — the "know me" core.
- Honest caveat for external use: single extraction model, single run
  so far; extraction-model sensitivity test scheduled. Numbers are
  Flash Lite answer-model, budget-pinned per our comparability rules.

## Positioning care

- Frame as capability ("bring-your-own-intelligence extraction"), never
  as a critique of specific competitors' architectures.
- Model-selection rationale in public materials: "early customer
  preference" + technical merits, per standing wording rule.
- Do not cite the competitive scorecard externally until context-budget
  parity is documented alongside it (frontier framing: accuracy AND
  tokens-per-query, both axes, always).
