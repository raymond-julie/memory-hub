# Agent Memory Types

Classification axes with types, definitions, and primary sources.

## Axis 1: Temporal Scope (how long it lasts)

**Working Memory** - The current inference context: system prompt, conversation turns, tool results, retrieved documents. Discarded after each LLM call. The model sees only what the harness assembles for this turn; everything else is invisible to inference regardless of where it is stored [1].

**Short-Term / Session Memory** - Full conversation history within one session. Survives across turns but not across sessions. In modern agent frameworks, this is implemented via checkpointing (LangGraph) or conversation buffers rather than dedicated memory classes [2].

**Long-Term Memory** - Persists across sessions. Facts, preferences, decisions. The storage mechanism (files, vector DB, graph) is an orthogonal choice [1].

## Axis 2: Cognitive Type (what kind of knowledge)

This is the taxonomy from cognitive science, combining Tulving's episodic/semantic distinction [3] with Cohen and Squire's procedural/declarative split [4], adapted for agents.

**Semantic Memory** - General knowledge independent of personal experience. "FastAPI uses Pydantic for validation." What things are [5].

**Episodic Memory** - Records of specific experiences tied to time and context. "On May 15, user approved the schema change because of the compliance deadline." What happened [6].

**Procedural Memory** - How to do things. Workflows, standing instructions, step-by-step recipes. "When deploying, always run migrations before rolling out new pods" [5].

Note: From the LLM's perspective, all context is processed identically regardless of type label. These categories are most useful as extraction heuristics (what to remember), not as storage schema or retrieval axes. See [Tiered Retrieval and Associative Memory](tiered-retrieval-and-associative-memory.md) for a fuller treatment.

## Axis 3: Storage / Retrieval Strategy (how it's stored and found)

**File-Based Memory** - Memories as files in a directory structure. Agent runtimes naturally traverse paths. Examples: CLAUDE.md, Cursor rules, .github/copilot-instructions.md [7].

**Vector/Embedding Memory** - Content embedded and stored in a vector database. Retrieved via semantic similarity search. Scales well but loses structure [1].

**Graph Memory** - Entities and relationships in a knowledge or property graph. Preserves structure, supports traversal and reasoning. Graphiti implements a bi-temporal model tracking event time T (when something happened) and ingestion time T' (when the system learned about it), with four timestamps per fact (t'_created, t'_expired, t_valid, t_invalid) enabling temporal queries and fact invalidation [8].

**Hybrid** - Most production systems combine strategies. Graphiti uses graph + vector + community summaries [8]. Mem0 uses vector + graph [5].

## Axis 4: Architectural Role (what function it serves)

**World Memory** - Objective external facts, separated from the agent's own experiences. Latimer et al. formalize this as a "World Network" distinct from experience, opinion, and observation networks [9].

**Behavioral Memory** - Agent personality, communication style, tool preferences. Enables reconstructing an agent's behavior from memory alone. Related to Karpathy's "system prompt learning" concept [10] and LangMem's procedural memory (self-modifying prompt instructions) [4]. Liao et al.'s STEAM framework [11] demonstrates that structured atomic memory units outperform single unstructured summaries for modeling multi-faceted behavioral patterns, though their work focuses on user behavior modeling rather than agent self-knowledge. The term "behavioral memory" as applied to agent self-representation is emerging usage, not yet a formal category in the literature.

**Observational Memory** - Compressed summaries synthesized from raw interactions by background processes (e.g., observer and reflector agents). Mastra's implementation achieves 3-40x compression depending on workload type [12].

**Reasoning Traces** - Records of how the agent solved past problems. Enables finding analogous reasoning patterns. Reflexion [13] demonstrated that agents storing verbal self-reflections improve on subsequent attempts; Retrieval-of-Thought [14] formalizes a thought graph with reward-guided traversal for organizing and retrieving past reasoning.

## Cross-cutting: Retrieval Resolution

Orthogonal to all four axes is the question of *how much* of a memory to surface. Most systems treat retrieval as binary (in context or not), but a resolution gradient from stub (one-line pointer, ~10 tokens) to summary (~50-100 tokens) to full hydration (complete content) enables dramatically better context budget utilization. See [Tiered Retrieval and Associative Memory](tiered-retrieval-and-associative-memory.md) for the full argument and proposed API primitives.

## Cross-cutting: Governance

Who can read this memory, who can write it, what is the retention policy, how do we audit changes, how do we delete on request. This axis does not appear in cognitive-science taxonomies because it is an enterprise concern, not a model-of-the-mind concern. It is also the axis that turns "agent memory" from a research topic into a procurement decision. See the [Agent Memory Protocol RFC](agent-memory-protocol-rfc.md) for a detailed treatment.

---

## References

### Cognitive Science

[3] Tulving, E. (1972). Episodic and semantic memory. In E. Tulving & W. Donaldson (Eds.), *Organization of Memory* (pp. 381-403). Academic Press. [APA PsycNet](https://psycnet.apa.org/record/1973-08477-007)

[4] Cohen, N. J., & Squire, L. R. (1980). Preserved learning and retention of pattern-analyzing skill in amnesia: Dissociation of knowing how and knowing that. *Science*, 210(4466), 207-209. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC2791502/)

### Agent Memory Systems

[1] Packer, C., Fang, V., et al. (2023). MemGPT: Towards LLMs as Operating Systems. [arXiv:2310.08560](https://arxiv.org/abs/2310.08560)

[2] LangChain. (2025). Memory overview. [docs.langchain.com/oss/python/concepts/memory](https://docs.langchain.com/oss/python/concepts/memory)

[5] Chhikara, P., et al. (2025). Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory. *ECAI 2025*. [arXiv:2504.19413](https://arxiv.org/abs/2504.19413). Memory types: [docs.mem0.ai/core-concepts/memory-types](https://docs.mem0.ai/core-concepts/memory-types)

[6] Qi, B., et al. (2026). Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging Frontiers. [arXiv:2603.07670](https://arxiv.org/abs/2603.07670)

[7] Anthropic. (2025). Claude Code: Memory. [docs.anthropic.com/en/docs/claude-code/memory](https://docs.anthropic.com/en/docs/claude-code/memory)

[8] Rasmussen, P., Paliychuk, P., Beauvais, T., Ryan, J., & Chalef, D. (2025). Zep: A Temporal Knowledge Graph Architecture for Agent Memory. [arXiv:2501.13956](https://arxiv.org/abs/2501.13956). Bi-temporal model detail in [HTML version](https://arxiv.org/html/2501.13956v1), Section 3.

[9] Latimer, C., Boschi, N., Neeser, A., Bartholomew, C., Srivastava, G., Wang, X., & Ramakrishnan, N. (2025). Hindsight is 20/20: Building Agent Memory that Retains, Recalls, and Reflects. [arXiv:2512.12818](https://arxiv.org/abs/2512.12818)

[10] Karpathy, A. (2025). "System prompt learning" concept. [X post, May 10, 2025](https://x.com/karpathy/status/1921368644069765486). Proposes a third learning paradigm alongside pretraining (knowledge) and finetuning (habitual behavior): explicit self-authored instructions that the agent refines through experience.

[11] Liao, Y., Wu, L., Hou, M., Wang, Y., Wu, H., & Wang, M. (2026). From Atom to Community: Structured and Evolving Agent Memory for User Behavior Modeling. [arXiv:2601.16872](https://arxiv.org/abs/2601.16872). Note: this paper addresses *user* behavior modeling via structured atomic memory units, not agent self-representation. Cited for the structural insight that atomic memory units outperform single unstructured summaries.

[12] Mastra. (2026). Observational Memory: 95% on LongMemEval. [mastra.ai/research/observational-memory](https://mastra.ai/research/observational-memory)

[13] Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K. R., & Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. *NeurIPS 2023*.

[14] Retrieval-of-Thought: Efficient Reasoning via Reusing Thoughts. [arXiv:2509.21743](https://arxiv.org/abs/2509.21743)
