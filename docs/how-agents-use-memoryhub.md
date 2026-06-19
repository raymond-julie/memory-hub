# How Agents Use MemoryHub

MemoryHub doesn't extract memories automatically. It injects instructions and context into the agent's prompt, gives the agent tools to read and write memories, and trusts the agent's judgment about what's worth persisting. This document explains the concrete mechanism: what text gets injected, where it appears, and how the agent decides to act on it.

For the API and tool reference, see the [Agent Integration Guide](agent-integration-guide.md). For the hook setup walkthrough, see the [Hooks Integration Guide](hooks-integration.md).

## The three injection points

MemoryHub establishes itself in the agent's context through three mechanisms, each operating at a different layer of Claude Code's architecture. Together, they ensure the agent knows MemoryHub exists, has relevant memories pre-loaded, and has tools available to read and write more.

### 1. The rule file: instructions the agent must follow

Claude Code loads every `.md` file in `.claude/rules/` at the start of each session and includes their content in the system prompt. MemoryHub uses this to inject a rule file called `memoryhub-loading.md` that tells the agent what to do with memory.

The rule file is generated, not hand-written. Running `memoryhub config init` reads `.memoryhub.yaml` and produces `.claude/rules/memoryhub-loading.md` tailored to the project's loading pattern. Here is what the agent sees (abridged from the actual file in this repo):

```
# MemoryHub Loading: Lazy + Rebias on Pivot

This project uses MemoryHub for persistent, centralized agent memory across
conversations. You MUST use it.

## At session start

Check for a <memoryhub-context> block in your conversation context.
If present, the SessionStart hook has pre-loaded project and user
memories -- use them as your working set. ...

## During the session -- watch for pivots

A pivot is any of:
1. Subsystem change -- the user changes topic to a different area
2. Unknown concept -- the user references a term not in your working set
3. Explicit switch -- the user says "let's switch to..."

When you detect a pivot, call search_memory with a query for the new topic.

## Memory hygiene

- DO write preferences, decisions, architectural choices, tool
  configuration, and workflow patterns.
- Skip ephemeral things like "user asked me to read a file."
- Use update_memory (not write_memory) to revise an existing entry.
- Set weights deliberately: 1.0 for critical policies, 0.5-0.7 for
  nice-to-know context.
```

This is how the agent "knows" that MemoryHub is where it should store and retrieve memories. The rule is an instruction baked into the system prompt, at the same level as the project's CLAUDE.md and any other rules. The agent treats it the same way it treats any other project instruction -- it's not optional guidance, it's a directive.

The rule also tells the agent *when* to store memories. It doesn't say "store everything." It says: write preferences, decisions, architectural choices, and workflow patterns. Skip ephemeral actions. The agent exercises judgment about whether a given piece of information crosses that threshold. There is no automatic extraction pipeline -- the agent is the extraction pipeline.

### 2. The SessionStart hook: pre-loaded context

Rules tell the agent what to do, but the agent would still need to spend tool calls searching for relevant memories at the start of every session. The SessionStart hook eliminates that cost.

Claude Code supports hooks in `.claude/settings.json` that run shell commands on session events. MemoryHub registers a hook for `startup`, `compact`, and `clear` events:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [{
          "type": "command",
          "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/load-memories.sh",
          "timeout": 5
        }]
      }
    ]
  }
}
```

When a session starts, Claude Code runs `load-memories.sh`. The script reads the API key from `~/.config/memoryhub/api-key`, calls the CLI to search for relevant project memories, and prints the results to stdout. Claude Code captures that stdout and injects it into the conversation context before the agent sees the first user message.

The output uses a tagged format that the rule file references:

```
<memoryhub-context project="memory-hub">
- FastAPI is the preferred web framework for new Python projects.
- Use Podman, not Docker; Containerfile, not Dockerfile.
- Deploy scripts must run in main conversation context, never sub-agents.
- The MCP server uses the compact tool profile (2 tools) by default.
- Python is the primary language for AI/ML work and backend services.
</memoryhub-context>
```

No memory IDs, no timestamps, no weights. Just the content the agent needs. This is deliberate: structural metadata in the context window activates model reasoning about memory management instead of the user's task. The compact format keeps the agent focused on doing work, not managing its own memory infrastructure.

The hook completes in under a second and exits silently on any error (missing CLI, unreachable server, expired key). A failed hook never blocks a session from starting. The rule file includes a fallback path: if no `<memoryhub-context>` block is present, the agent falls back to manual tool calls.

### 3. The MCP tools: mid-session operations

The first two mechanisms handle session startup. For everything else -- searching for new context, writing memories, updating existing ones, reporting contradictions -- the agent uses MCP tools.

When the MCP server is configured in Claude Code's settings, Claude Code discovers its tools at startup and makes them available in the agent's tool list. The agent sees tool descriptions like:

```
register_session(api_key)
  Register this session with your API key. Call this once at the start
  of every conversation to establish your identity.

memory(action, query?, content?, scope?, ...)
  All-in-one memory operations. Call register_session first.
  Read actions: search, list, read, similar, relationships, ...
  Write actions: write, update, delete, set_focus, relate, ...
```

The agent calls these tools the same way it calls any other tool -- `Read`, `Bash`, `Edit`. The MCP server handles authentication, scope enforcement, and storage. The agent doesn't know or care that memories are stored in PostgreSQL with pgvector embeddings; it just calls `memory(action="write", content="...", scope="user")` and gets a confirmation.

## What triggers a memory write

Nothing triggers a write automatically. The rule file gives the agent guidelines, and the agent decides. In practice, the agent writes a memory when it recognizes that information from the current conversation will be useful in a future session. Common triggers:

- The user states a preference ("use Podman, not Docker")
- A non-obvious decision is made ("we chose pgvector because PostgreSQL was already in the stack")
- A workflow pattern is established ("deploy scripts must never run in sub-agents")
- A lesson is learned the hard way ("file permissions must be 644 before container builds")
- The user explicitly says "remember this"

The agent does *not* write memories for:

- Ephemeral actions ("I read the README," "I ran pytest")
- Things already captured in committed documentation (CLAUDE.md, README)
- Transient debugging context that won't matter next session
- Conversation logistics ("the user asked me to explain X")

The decision is always the agent's. Different agents (or the same agent in different sessions) may make different judgments about the same information. This is by design -- MemoryHub provides the infrastructure and the guidelines, not a rigid extraction pipeline.

## How it all fits together

A typical session flow:

1. Session starts. Claude Code runs `load-memories.sh`, which searches MemoryHub and prints a `<memoryhub-context>` block.
2. Claude Code loads `.claude/rules/memoryhub-loading.md` into the system prompt.
3. The agent sees both: pre-loaded memories as context, and instructions about how to manage memory.
4. The user asks a question. The agent uses pre-loaded memories to inform its response.
5. Mid-session, the user pivots to a new subsystem. The agent detects the pivot (per the rule), calls `memory(action="search", query="new topic")`, and adds results to its working set.
6. The user makes a decision worth remembering. The agent calls `memory(action="write", content="...", scope="user")`.
7. Session ends. Memories persist in MemoryHub. The next session (possibly days later, possibly by a different agent) picks them up via the hook.

## Setting it up in a new project

Three commands:

```bash
pip install memoryhub-cli
memoryhub login
memoryhub config init
```

`memoryhub login` stores your API key and server URL in `~/.config/memoryhub/`. `memoryhub config init` runs an interactive wizard that asks about your project's session shape and generates three files: `.memoryhub.yaml`, `.claude/rules/memoryhub-loading.md`, and `.claude/hooks/load-memories.sh`. It also merges the hook configuration into `.claude/settings.json`.

Commit `.memoryhub.yaml`, the rule file, and the hook script to your repository. Do not commit credentials -- those stay in `~/.config/memoryhub/`.

After setup, the next Claude Code session in that project will automatically load relevant memories at startup and have the MCP tools available for mid-session operations.
