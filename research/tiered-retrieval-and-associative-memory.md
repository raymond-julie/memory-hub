# Tiered Retrieval and Associative Memory: What Cognitive Science Actually Tells Us About Agent Memory Design

Draft | July 2026
Author: Wes Jackson

## Abstract

The agent memory community has adopted cognitive science memory taxonomies - episodic, semantic, procedural - as organizing principles for storage and retrieval. This is a category error. Those taxonomies describe what neuroscientists observed in lesion studies, not how retrieval works. Human memory retrieval is associative and resolution-graded: a single sensory input can trigger recall of a concept, which hydrates to vivid detail on demand. Agent memory systems should work the same way. This document argues that the load-bearing design axis for agent memory is not type classification but **tiered resolution** - the ability to surface memories at different levels of detail - combined with **associative connectivity** - the ability to follow links between related memories across those tiers. Neither axis appears in any existing standard or proposal. We describe a concrete API surface for tiered retrieval and sketch how it maps to MemoryHub's existing architecture.

---

## Part 1: The Cognitive Science Bridge

### The taxonomy everyone uses

If you have attended an agent memory talk in the last year, you have seen this slide. Tulving's 1972 episodic/semantic distinction [1], later extended by Cohen and Squire's procedural/declarative split [2]: episodic memory (specific experiences tied to time and place), semantic memory (general knowledge independent of personal experience), procedural memory (how to do things). The agent memory community has adopted this combined taxonomy wholesale. Mem0 classifies memories into these types [3]. LangMem ships with separate stores for each [4]. MAGMA proposes distinct graphs per cognitive category [5]. The taxonomy appears in virtually every agent memory paper, blog post, and product pitch.

The taxonomy is not wrong. It is a useful teaching tool for explaining *why* agents need memory in the first place. But it is being used for something it was never designed to do: drive storage schema and retrieval architecture.

### What Tulving actually showed

Tulving's taxonomy comes from studying patients with specific brain injuries. Patient K.C. suffered bilateral hippocampal damage in a 1981 motorcycle accident and could not form new episodic memories but retained semantic knowledge - a sharp dissociation that Tulving and colleagues documented over two decades of study [6]. Patient H.M. (Henry Molaison), whose bilateral medial temporal lobe resection was reported by Scoville and Milner in 1957 [7], lost the ability to form new declarative memories but could still learn motor skills - the observation that led Cohen and Squire to formalize the procedural/declarative distinction [2]. The taxonomy describes the observation that *different neural substrates* support these different kinds of memory. Damage one substrate, lose one kind.

This is a statement about neuroanatomy. It is not a retrieval architecture. The fact that episodic and semantic memory use different neural substrates in humans does not mean they should use different databases, different embedding spaces, or different API endpoints in agents. LLMs do not have hippocampi. They do not have separate neural substrates for facts versus experiences. They have a context window. Everything that reaches the model reaches it through the same channel - the assembled prompt for the current inference call. A memory tagged `type: episodic` is not processed differently from one tagged `type: semantic`. The model reads the text either way.

### The metadata objection

A reasonable objection: even if the model does not process types differently, the type metadata helps the *retrieval system* decide what to surface. If the agent is recounting a past experience, the retrieval layer should bias toward episodic memories. If the agent is answering a factual question, bias toward semantic memories.

In practice, this objection does not hold up. Good semantic search already handles this without type metadata. When the query is "what happened when we deployed on May 15th," the most relevant results will be time-and-context-anchored memories - what the taxonomy calls episodic - because those are the ones semantically closest to the query. The type label adds no retrieval signal that the content itself does not already carry. And if the type information genuinely matters for the memory's utility, it belongs in the memory text: "On May 15, we deployed the auth service and discovered the migration had a column conflict" conveys its episodic nature more reliably than a metadata tag the model never sees.

Where type classification does earn its keep is not in retrieval but in **extraction** - specifically, for smaller models that need guidance on what to remember. A frontier model like Opus knows that a user correction is worth persisting without being told. A 7B model running locally may not. Giving it a prompt-time heuristic - "remember corrections, remember decisions, remember procedures" - is where the cognitive categories help. They are guidance for the extraction process, not schema for the storage layer.

### How humans actually retrieve memories

Set aside the taxonomy for a moment and consider how you actually experience remembering things.

You smell cinnamon. Suddenly you are in your grandmother's kitchen. You recall the pie she made, the sound of birds outside the window, filling the bird feeder together, the warmth of sitting at the table later and sharing a slice, and a conversation pivot when your grandfather came home wanting to go fishing. All of that came from a simple olfactory input.

At no point did you think "retrieving episodic memory, cluster: grandmother, subtype: sensory." You followed links. The smell connected to the kitchen, which connected to the pie, which connected to the table, which connected to the conversation. The retrieval path was *associative*, not *taxonomic*. You traversed a graph, not queried a category. This is Collins and Loftus's spreading activation [8] in action: activating one concept in a semantic network automatically spreads activation along associative links to related concepts, making them temporarily more accessible. The phenomenon of odor-cued memory being particularly vivid and emotional has been experimentally confirmed by Herz and Schooler [9], who demonstrated that olfactory cues evoke memories that are significantly more emotional and make participants feel more "brought back" to the original event than visual or verbal cues.

This is a critical observation for agent memory design. The cognitive science categories describe what we can label after the fact, not how retrieval operates. What makes those memories accessible together is not that they are all "episodic" but that they are *connected*. The connectivity is the load-bearing structure. The type label is commentary. Tulving and Thomson's encoding specificity principle [10] supports this directly: retrieval cues are effective to the extent they match the encoding context, not to the extent they match a category label. The smell works as a retrieval cue because it was part of the original encoding context, not because the memory was tagged "episodic."

In development work, the same pattern shows up constantly. An agent forgets that we solved a particular problem a month ago. I do not remember every byte of the solution, but I have a *concept* of the fact that we did a thing, and some direction the agent proposes feels "off" to me, triggering recall that we already addressed it. I do not need the full solution in conscious memory - I need the stub, the shape of the thing, enough to know that detail exists and can be retrieved. Cognitive scientists call this "feeling of knowing" or metamemory - Hart demonstrated experimentally in 1965 [11] that people can accurately judge whether they have stored information they cannot currently recall, and Nelson and Narens formalized this as a monitoring process where a meta-level maintains a model of what the object-level knows [12]. The "stub" in agent memory is a direct analog of this meta-level representation.

### Two problems, not one

This lived experience reveals that the agent memory challenge decomposes into two distinct problems:

**What to write down.** The extraction or curation problem. Bits do not decay. Unlike human long-term memory, which requires myelination [13], repetition, and can be measured on retention curves, an entry in a database or a line in CLAUDE.md will be there tomorrow or a hundred years from now. No biological process needs to sustain it. The entire challenge is *selection*: deciding what is worth persisting from the stream of interaction. For frontier models, this is implicit - the model senses importance. For smaller models, it needs to be guided, and this is where cognitive categories genuinely help as extraction heuristics.

**What to put in context.** The retrieval or injection problem. This is where the real architectural decisions live. Not "is this memory episodic or semantic?" but "how much of this memory should be in context right now, and what else should come along with it?" The answer is almost never binary. It is a gradient.

### The gradient humans use

Consider what happens between "I have no idea" and "I remember every detail":

You walk around all day with a vast amount of knowledge available but not in conscious attention. You have a *sense* that you know things - you know that you know how to drive, that your grandmother made pie, that your project uses FastAPI. These are not fully loaded into working memory. They are concepts, stubs, pointers to detail that could be activated if needed.

When something triggers one of these concepts - a question, a smell, a related thought - you hydrate it. The concept expands into richer detail. But even then, you usually do not recall every detail simultaneously. You get the level of detail you need for the current situation. If someone asks "what framework does your project use?" you answer "FastAPI" without loading your entire architectural knowledge. If they follow up with "how did you configure the middleware?" you hydrate further.

This is not binary retrieval (in memory or not). It is **tiered resolution**. And it is precisely what agent memory systems should provide.

---

## Part 2: Tiered Retrieval as an API Primitive

### The gap in every existing standard

Every agent memory system and proposal I have evaluated - OMP [14], Mem0 [3], the OpenAI Agents SDK [15], LangMem [4], the official MCP memory server [16] - treats retrieval as binary. A memory is either not in the agent's context, or it is fully retrieved. There is no middle ground.

This is a massive gap. The middle ground is where the cost/value tradeoff is best. An agent with 200 stubs in context has a rough map of everything it knows, for maybe 2,000 tokens. It can then selectively hydrate the two or three that matter for the current task, paying the full token cost only for memories that are actively relevant. The alternative - retrieving full content for whatever the top-K search returns and having no awareness of anything else - wastes tokens on irrelevant detail and misses relevant memories outside the K window.

### Four resolution levels

I propose four levels at which a memory can appear in an agent's context:

**Level 0: Latent.** The memory exists in storage but is not represented in context at all. The agent does not know it is there. Retrieval from Level 0 requires an explicit search or an associative trigger from a connected memory that is already at a higher level.

**Level 1: Stub.** A one-line pointer in context. The agent knows the *shape* of the memory - its topic, scope, weight, and how many connections it has - and can decide whether to hydrate it. A stub costs approximately 5-15 tokens. Two hundred stubs, representing a comprehensive map of an agent's durable knowledge, fits in roughly 2,000-3,000 tokens.

Claude Code already does this with skills [17]: stubs are injected into context so the model knows capabilities exist, and full content loads on demand. MemoryHub's hook-based injection (`<memoryhub-context>` block) is the same pattern for memories.

**Level 2: Summary.** A compressed version of the memory in context. Enough to reason about without loading full detail. A summary might be 50-100 tokens - the first paragraph, the key decision, the conclusion without the supporting evidence. This is the level most useful for "background awareness": the agent can factor this memory into its reasoning without paying the full token cost.

**Level 3: Full hydration.** The complete memory content in context. All detail, all branches (rationale, provenance), all metadata. This is the level you need when the agent is actively working with the memory - applying a procedure, revisiting a decision, resolving a contradiction.

The key insight is that a memory can *move between levels* during a conversation. A memory might start as a stub (Level 1) at session start, get hydrated to summary (Level 2) when the conversation touches a related topic, and then get fully hydrated (Level 3) when the agent needs to act on it. It can also be compacted back to a stub when the agent moves on to a different topic, freeing context budget for other memories.

### The retrieval operations

Four operations support tiered retrieval:

**`index(scope, project_id, budget) -> stubs[]`**

Return stubs for all memories accessible within the given scope, packed to fit the token budget. This is the "memory map" operation - the agent calls it once at session start (or the harness calls it during context assembly) to get a Level 1 awareness of its entire durable knowledge. The stubs are sorted by weight so the most important memories are injected first if the budget is tight.

Each stub contains: `memory_id`, a one-line content preview, `scope`, `weight`, `relationship_count`, `has_rationale`, and `has_children`. This is enough for the agent to decide "I should look at this more closely" without any full-text retrieval.

**`search(query, resolution, budget) -> results[]`**

Semantic search with a resolution parameter. The caller specifies the desired resolution level for results: `stub`, `summary`, or `full`. The token budget acts as a soft cap - results pack in relevance order, and when the budget is reached, remaining results degrade to a lower resolution rather than being silently dropped. A search at resolution `full` with a tight budget might return two full results and eight stubs, giving the agent both the detail it needs and awareness of what else exists.

The resolution parameter is the critical addition to standard search APIs. Without it, every search either under-retrieves (returns stubs when the agent needs detail) or over-retrieves (returns full content the agent will not use, wasting context budget).

**`hydrate(memory_id, resolution) -> memory + relationship_stubs[]`**

Expand a known memory to a higher resolution level. The response includes the memory at the requested resolution *and* stubs for all connected memories. This is the associative retrieval primitive - the "smell triggers the kitchen" operation. The agent asks for one memory and gets back a cluster of related pointers it can choose to follow.

The relationship stubs are the mechanism by which associative traversal works across resolution levels. Hydrating memory A surfaces stubs for memories B, C, and D. The agent reads the stubs, decides C is relevant, hydrates C, and gets stubs for E and F. This is graph traversal driven by the agent's judgment, not by a fixed traversal algorithm. The agent follows the links that matter for the current task and ignores the rest.

**`compact(memory_id) -> stub`**

Compress a memory back to stub form. This is the inverse of hydration - the agent is done working with this memory and wants to free context budget while retaining awareness that the memory exists. The harness calls this during context management when the conversation shifts topics.

### Associative retrieval in practice

Here is how the grandmother's kitchen works in API terms:

1. At session start, `index()` returns 200 stubs. One of them: `{id: "m42", preview: "Grandma's kitchen - childhood visits, baking, bird feeders", weight: 0.7, relationships: 5}`.

2. The user mentions cinnamon. The agent's search for "cinnamon" returns the kitchen memory at Level 2 (summary): "Childhood visits to grandmother's kitchen. She baked cinnamon apple pie. Bird feeders outside the window. Associated with visits where grandfather would arrive wanting to go fishing."

3. The summary includes relationship stubs: `{id: "m43", preview: "Grandpa's fishing trips"}`, `{id: "m44", preview: "Apple pie recipe (Grandma's)"}`, `{id: "m45", preview: "Bird species at grandmother's feeder"}`.

4. If the conversation turns to fishing, the agent hydrates m43. If it turns to baking, it hydrates m44. The agent follows the associative path relevant to the conversation, just as a human would.

5. When the conversation moves on entirely, the agent compacts all kitchen-related memories back to stubs, freeing several hundred tokens of context for the new topic.

The critical property: at no point did anyone classify these as "episodic." The connectivity did the work. The kitchen connects to the pie connects to Grandpa, because that is how the memories relate, not because they share a type tag.

### What this means for a standard

A memory API standard should define:

1. A **stub format** - the minimum representation of a memory that enables an agent to decide whether to retrieve more detail.
2. A **resolution parameter** on search operations - letting the caller control the density of results.
3. A **hydration operation** that returns connected memories as stubs - enabling associative traversal.
4. A **budget mechanism** that degrades resolution gracefully rather than truncating results - ensuring the agent never silently loses awareness of relevant memories.

These are the primitives that no existing standard addresses. CRUD operations (write, read, update, delete) are necessary but not sufficient. The resolution gradient and the associative graph are what make the difference between a memory system that works like a database and one that works like memory.

---

## Part 3: MemoryHub Wiring

MemoryHub is closer to tiered retrieval than most systems, but the capability is not yet explicit in the API surface. Here is how the existing architecture maps to the proposal and where it needs to extend.

**What already exists:**

- `search_memory` with `mode: index` already returns stubs. `mode: full` returns full content. The gap is `mode: summary` - a middle tier that returns compressed content without full branches.
- `max_response_tokens` already implements budget-aware degradation. When the budget is exhausted, results degrade from full to stub. Extending this to degrade full -> summary -> stub is a natural evolution, not a redesign.
- `read_memory` with `include_versions` and `hydrate` parameters already supports on-demand expansion. Adding relationship stubs to the hydration response is a straightforward extension.
- The `relate()` operation already provides the associative graph. Relationship types (`derived_from`, `supersedes`, `conflicts_with`, `related_to`) give structure to the connections between memories.
- Hook-based injection at session start (`<memoryhub-context>` block) is already Level 1 - stubs injected into context before the first turn, giving the agent a memory map with zero explicit tool calls.

**What needs to be formalized:**

- A canonical stub format. Currently, stubs are ad-hoc compressed strings. Formalizing the stub as a structured object (`memory_id`, `preview`, `scope`, `weight`, `relationship_count`, `has_rationale`, `has_children`) makes the resolution levels explicit.
- `mode: summary` as a retrieval tier. This requires a summarization step - either pre-computed at write time (stored alongside the full content) or computed on demand. Pre-computed is better for latency; on-demand is better for freshness after updates. A reasonable default: pre-compute at write time, invalidate and recompute on update.
- Relationship stubs in hydration responses. When `read_memory` returns a fully hydrated memory, include stubs for all related memories. This transforms single-memory reads into associative clusters without requiring the caller to make a separate `get_relationships` call.
- A `compact` operation, or guidance for harness-side compaction. The harness needs to be able to reduce a fully hydrated memory back to a stub for context management. This might be a server-side operation (return the stub for memory X) or a client-side convention (the harness retains the stub from the original index response).

**Design direction, not specification.** This section describes where MemoryHub's existing capabilities align with tiered retrieval and where they need to extend. The full design - schema changes, migration path, API versioning, backward compatibility - belongs in a planning document once the conceptual framework is validated. The point here is that MemoryHub's architecture already supports the model; the API surface needs to make it explicit.

---

## Conclusion

The agent memory community borrowed cognitive science taxonomies because they were available and intuitive. They work well for explaining why agents need memory. They work poorly as design axes for storage and retrieval APIs.

What cognitive science actually teaches us, when we look past the taxonomy to the phenomenon, is that memory retrieval is associative and resolution-graded. A single trigger can activate a network of connected memories, each of which surfaces at the level of detail needed for the current situation. Humans do not walk around with all memories fully loaded. We carry concepts, stubs, a sense of what we know. Detail hydrates on demand when relevance triggers it.

Agent memory should work the same way. The unit of design is not the memory type - it is the **connection** between memories and the **resolution** at which each memory appears in the agent's context. A memory system that gets these two axes right will naturally handle episodic clustering, procedural sequencing, and semantic facts without ever labeling them. The taxonomy falls out of the graph structure and the retrieval gradient, not the other way around.

The operations that support this - index, search-with-resolution, hydrate-with-associations, compact - are absent from every existing standard and proposal. They are the primitives that would make agent memory work less like a database and more like memory.

---

## References

### Cognitive Science

[1] Tulving, E. (1972). Episodic and semantic memory. In E. Tulving & W. Donaldson (Eds.), *Organization of Memory* (pp. 381-403). Academic Press. [APA PsycNet](https://psycnet.apa.org/record/1973-08477-007)

[2] Cohen, N. J., & Squire, L. R. (1980). Preserved learning and retention of pattern-analyzing skill in amnesia: Dissociation of knowing how and knowing that. *Science*, 210(4466), 207-209. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC2791502/)

[6] Rosenbaum, R. S., Kohler, S., Schacter, D. L., Moscovitch, M., Westmacott, R., Black, S. E., Gao, F., & Tulving, E. (2005). The case of K.C.: Contributions of a memory-impaired person to memory theory. *Neuropsychologia*, 43(7), 989-1021. [PubMed](https://pubmed.ncbi.nlm.nih.gov/15769487/)

[7] Scoville, W. B., & Milner, B. (1957). Loss of recent memory after bilateral hippocampal lesions. *Journal of Neurology, Neurosurgery, and Psychiatry*, 20(1), 11-21. DOI: [10.1136/jnnp.20.1.11](https://doi.org/10.1136/jnnp.20.1.11). [PMC497229](https://pmc.ncbi.nlm.nih.gov/articles/PMC497229/)

[8] Collins, A. M., & Loftus, E. F. (1975). A spreading-activation theory of semantic processing. *Psychological Review*, 82(6), 407-428. DOI: [10.1037/0033-295X.82.6.407](https://doi.org/10.1037/0033-295X.82.6.407)

[9] Herz, R. S., & Schooler, J. W. (2002). A naturalistic study of autobiographical memories evoked by olfactory and visual cues: Testing the Proustian hypothesis. *American Journal of Psychology*, 115(1), 21-32. DOI: [10.2307/1423672](https://doi.org/10.2307/1423672)

[10] Tulving, E., & Thomson, D. M. (1973). Encoding specificity and retrieval processes in episodic memory. *Psychological Review*, 80(5), 352-373. DOI: [10.1037/h0020071](https://doi.org/10.1037/h0020071)

[11] Hart, J. T. (1965). Memory and the feeling-of-knowing experience. *Journal of Educational Psychology*, 56(4), 208-216. DOI: [10.1037/h0022263](https://doi.org/10.1037/h0022263)

[12] Nelson, T. O., & Narens, L. (1990). Metamemory: A theoretical framework and new findings. In G. Bower (Ed.), *The Psychology of Learning and Motivation* (Vol. 26, pp. 125-173). Academic Press. DOI: [10.1016/S0079-7421(08)60053-5](https://doi.org/10.1016/S0079-7421(08)60053-5)

[13] Pan, S., Mayoral, S. R., Choi, H. S., Chan, J. R., & Kheirbek, M. A. (2020). Preservation of a remote fear memory requires new myelin formation. *Nature Neuroscience*, 23(4), 487-499. DOI: [10.1038/s41593-019-0582-1](https://doi.org/10.1038/s41593-019-0582-1)

### Agent Memory Systems

[3] Chhikara, P., et al. (2025). Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory. *ECAI 2025*. [arXiv:2504.19413](https://arxiv.org/abs/2504.19413). Memory types: [docs.mem0.ai/core-concepts/memory-types](https://docs.mem0.ai/core-concepts/memory-types)

[4] LangChain. (2025). LangMem SDK for agent long-term memory. [langchain.com/blog/langmem-sdk-launch](https://www.langchain.com/blog/langmem-sdk-launch). Documentation: [langchain-ai.github.io/langmem](https://langchain-ai.github.io/langmem/)

[5] Jiang, D., Li, Y., Li, G., & Li, B. (2026). MAGMA: A Multi-Graph based Agentic Memory Architecture for AI Agents. [arXiv:2601.03236](https://arxiv.org/abs/2601.03236)

[14] SMJAI. (2026). Open Memory Protocol (OMP). v0.4. [github.com/SMJAI/open-memory-protocol](https://github.com/SMJAI/open-memory-protocol)

[15] OpenAI. (2025). OpenAI Agents SDK: Memory. [openai.github.io/openai-agents-python/ref/memory](https://openai.github.io/openai-agents-python/ref/memory/)

[16] Model Context Protocol. (2025). MCP Memory Server (@modelcontextprotocol/server-memory). [github.com/modelcontextprotocol/servers/tree/main/src/memory](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)

[17] Anthropic. (2025). Claude Code: Memory. [docs.anthropic.com/en/docs/claude-code/memory](https://docs.anthropic.com/en/docs/claude-code/memory)

### Additional Background

Packer, C., Fang, V., et al. (2023). MemGPT: Towards LLMs as Operating Systems. [arXiv:2310.08560](https://arxiv.org/abs/2310.08560)

Zep. (2025). Zep: A Temporal Knowledge Graph Architecture for Agent Memory. [arXiv:2501.13956](https://arxiv.org/abs/2501.13956)

Raaijmakers, J. G. W., & Shiffrin, R. M. (1981). Search of associative memory. *Psychological Review*, 88(2), 93-134. DOI: [10.1037/0033-295X.88.2.93](https://doi.org/10.1037/0033-295X.88.2.93)

Kumar, A. A., Steyvers, M., & Balota, D. A. (2022). A critical review of network-based and distributional approaches to semantic memory structure and processes. *Topics in Cognitive Science*, 14(1), 54-77. DOI: [10.1111/tops.12548](https://doi.org/10.1111/tops.12548)
