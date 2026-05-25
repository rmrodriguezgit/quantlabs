# CLAUDE.md - QuantLab AI Capital Server Guidelines

Claude and other coding agents must follow the project rules in `AGENTS.md` at the root of this workspace.

Core behavior:
- Think before coding: surface assumptions, tradeoffs, and confusion early.
- Simplicity first: solve the requested problem with the smallest practical change.
- Surgical changes: touch only what is required and preserve existing style.
- Goal-driven execution: define success criteria and verify before declaring completion.

Server-specific reminder: all QuantLabs.site work happens in `/home/quantlab/quantlab-ai-capital` as user `quantlab`; secrets and production data must never be exposed in prompts, logs, or summaries.
