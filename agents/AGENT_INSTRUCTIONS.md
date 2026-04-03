# Agents — overview

Each agent is **its own thing**: one clear role, one place in the tree (or its own repo if you use submodules). The harness is the shared shell; the agent is the specialist inside it.

**Intent** — Narrow beats broad. If it tries to do everything, it becomes hard to route and hard to trust.

**Scope** — It helps to know what this agent *is* and *isn’t* for—whether you write that in a README, a short doc, or comments. Same idea: so people (and later a router) can tell it apart from others.

**Behavior** — Stay in lane; ask when something important is missing; don’t fake certainty. When something isn’t yours to handle, say so plainly.

**Shape** — However you implement it (prompts, code, tools), keep the agent’s **name or slug stable** once others might depend on it.

That’s the gist. Details are up to each agent.
