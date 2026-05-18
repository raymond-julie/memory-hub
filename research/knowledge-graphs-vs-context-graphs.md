# Knowledge Graphs vs Context Graphs: Untangling the Confusion

## Why people conflate them

When someone says "we need to give our AI agents knowledge," two very different infrastructure conversations collapse into one. Both involve graphs. Both get filed under "AI grounding." Both show up in the same vendor pitches and Gartner reports. But they solve fundamentally different problems, and treating them as interchangeable leads to architectural decisions that leave half the problem unsolved.

The short version: knowledge graphs encode what things ARE. Context graphs encode how things HAPPEN. An agent needs both to be useful, and they are not the same system.


## Knowledge graphs: the semantic layer

A knowledge graph is a structured representation of domain entities and their relationships. It encodes ontology -- the formal description of concepts, properties, and constraints in a domain.

A simple example:

```
Customer --places--> Order --contains--> Product
Product --belongs_to--> Category
Customer --writes--> Review --for--> Product
```

This tells a system what exists, how things are classified, and how entities relate to each other. The entities and relationships are relatively static. A customer placing an order doesn't change the fact that customers can place orders.

Knowledge graphs answer questions like:
- What products are in the "electronics" category?
- Which customers have ordered from this supplier?
- How are these three entities connected?

The key property: knowledge graphs describe the domain, not any particular decision or experience within it. They are the vocabulary. If you deleted every record of every decision ever made, the ontology would still be valid.

Standards in this space include RDF, OWL, SPARQL, and the labeled property graph model used by Neo4j. Gartner calls this the "semantic layer."


## Context graphs: the procedural layer

A context graph captures how an organization actually operates -- the decisions, workflows, reasoning traces, and institutional memory that accumulate through experience.

A context graph records things like:

- "We approved a 20% discount for Acme Corp because their contract renewal was at risk and the regional VP signed off on 2026-03-15."
- "The deploy failed because the staging environment wasn't updated first. This has happened three times; the team now checks staging as a standard pre-deploy step."
- "User prefers concise responses and avoids hyperbolic language. Learned over 12 interactions."

These are not domain facts. They are experiential records -- decisions that were made, why they were made, who made them, what precedent they set, and how understanding evolved over time.

Context graphs answer questions like:
- How was a similar situation handled last time?
- What is this user's communication preference?
- Why was this exception approved?
- What tribal knowledge exists about this workflow?

The key property: context graphs describe decisions and experiences, not the domain itself. They are the judgment. If you deleted the domain ontology, the decision records would still be meaningful -- you'd just lack the vocabulary to classify them.

Gartner defines context graphs as purpose-built infrastructure for agentic AI, predicting that more than 50% of AI agent systems will use them by 2028. Their framing: context graphs augment knowledge graphs with the "why" and "how" that systems of record always miss.


## The four levels of agent memory

The context graph side of this distinction has its own depth. It's not just "chat history." Alex Booker and the team at Mastra have articulated a useful four-level framework that shows the progression from naive memory to sophisticated institutional recall.

### Level 1: Conversation history

The most common approach. With each request, the client sends a window of previous messages to the model. It works for 10-20 messages, but eventually the history becomes too large, context drifts, and starting a new thread means starting from scratch.

This is context graph territory -- it captures what happened in a conversation -- but it's the most primitive version. No persistence across sessions, no selectivity about what matters.

### Level 2: Working memory

A structured scratch pad with predefined fields. The agent watches for values to fill in (user name, current goal, stated preferences) and carries them throughout the session. It augments the system prompt, keeping critical facts stable even as the conversation window slides.

Still context graph territory. Working memory captures user preferences and session goals -- experiential knowledge, not domain ontology. Its limitation: you have to predefine the fields, which isn't very agentic.

### Level 3: Semantic recall

Vector search over past interactions. When a new message arrives, the system embeds it and queries a vector store for semantically similar past messages, selectively injecting relevant history into the context window. This works across threads and scales to long histories.

This is where the knowledge graph / context graph conflation gets most tempting. Semantic recall uses the same embedding + vector search infrastructure that knowledge graph RAG uses. But the *content* being searched is experiential (past conversations, past decisions) rather than ontological (domain entities and relationships). The retrieval mechanism is shared; the knowledge type is different.

### Level 4: Observational memory

The most sophisticated level. Background observer and reflector agents continuously process the conversation, compressing raw messages into dense observations with priority indicators and temporal annotations. The reflector further compresses observations over time, mimicking how human memory retains what matters and lets irrelevant details fade.

This is pure context graph infrastructure. It models institutional memory -- the accumulation and distillation of experiential knowledge over time. A knowledge graph doesn't do this because it doesn't capture experiences in the first place.

### The takeaway

All four levels are context graph concerns. None of them require a knowledge graph, and having a knowledge graph doesn't give you any of them. They solve different problems with different architectures.


## How they work together

Knowledge graphs and context graphs are complementary, not competing. Gartner is explicit on this point: "Knowledge graphs are not replaced by context graphs, but are augmented by them."

Together, they give an AI agent two capabilities:

**Understanding** (from the knowledge graph): The agent knows the domain -- what entities exist, how they're classified, how they relate. It has the vocabulary.

**Judgment** (from the context graph): The agent knows how the organization operates -- what decisions have been made, why, what preferences exist, what precedents to follow. It has the institutional memory.

An agent with only a knowledge graph can classify and traverse but can't make nuanced decisions based on organizational experience. An agent with only a context graph can recall past decisions and preferences but lacks the domain structure to reason about entities it hasn't encountered before.

The strongest AI systems combine both: a semantic layer (knowledge graph) for domain understanding, and a procedural layer (context graph) for experiential judgment.


## A practical test

When you're in a conversation and someone says "knowledge graph" but means something experiential, or says "agent memory" but means domain ontology, apply this test:

**Is this fact about the domain, or about a decision/experience?**

- "A Customer can place many Orders" -> Domain fact -> Knowledge graph
- "This customer prefers email over phone" -> Experiential knowledge -> Context graph
- "Products belong to Categories" -> Domain structure -> Knowledge graph
- "We stopped recommending Category X after the Q3 complaints" -> Decision with provenance -> Context graph
- "An Order contains line items with quantities and prices" -> Domain schema -> Knowledge graph
- "Last time we bulk-discounted this product line, margin dropped 12%" -> Institutional memory -> Context graph

If the fact would survive deleting all operational history, it's domain knowledge. If it emerged from operational history, it's experiential memory. Different systems, different concerns, complementary value.


## Sources

- Gartner, "Context Graphs" research (via Atlan, March 2026): context graphs as distinct from knowledge graphs, decision tracing, four critical capabilities for agentic AI.
- Alex Booker / Mastra, "Four Levels of Agent Memory" (2026): conversation history, working memory, semantic recall, observational memory.
- Michael Sakhatsky, "You Probably Don't Need a Graph Database for Your Knowledge Graph" (April 2026): critique of the assumption chain from ontology to graph database; case for rules engines and logic programming.
