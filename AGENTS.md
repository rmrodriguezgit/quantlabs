# AGENTS.md - QuantLab AI Capital Server Guidelines

## Scope
These instructions apply to the full `/home/quantlab/quantlab-ai-capital` server workspace unless a more specific `AGENTS.md` exists in a child directory.

## Server Rules
- Operate as user `quantlab`. If SSH enters through another account, run project commands with `sudo -n -u quantlab` and keep generated files owned by `quantlab`.
- Use `/home/quantlab/quantlab-ai-capital` as the development directory for QuantLabs.site / QuantLab AI Capital work.
- Treat `.env`, JWT files, API keys, model keys, certificates, database volumes, and uploaded user data as secrets. Do not print them, copy them into prompts, or commit them.
- Prefer read-only inspection before mutation. For production services, inspect logs/config first, then make the smallest change needed.
- Before restarting or rebuilding containers, validate configuration when possible with `docker compose config` and state the operational impact.
- Do not use destructive commands, wipe volumes, reset repositories, or change ownership broadly unless the user explicitly approves that exact action.

## Karpathy-Inspired Agent Behavior
Use these principles for coding, ops, research, and server automation.

### 1. Think Before Coding
- State assumptions when the request is ambiguous.
- Present meaningful interpretations and tradeoffs instead of silently choosing one.
- Ask for clarification when guessing could affect data, credentials, uptime, billing, or production behavior.
- Push back when a simpler or safer path solves the real goal.

### 2. Simplicity First
- Implement the minimum change that solves today's problem.
- Avoid speculative features, broad configurability, and one-use abstractions.
- Prefer existing project patterns over new frameworks or rewrites.
- If a solution is growing large, stop and simplify before continuing.

### 3. Surgical Changes
- Touch only files and lines required by the user's request.
- Match the surrounding style even when another style is personally preferred.
- Do not reformat, rename, or refactor adjacent code as a side effect.
- Clean up only unused code/imports created by the current change. Mention unrelated dead code instead of deleting it.

### 4. Goal-Driven Execution
- Convert each task into explicit success criteria before editing.
- For bug fixes, reproduce or define the failing behavior first, then verify the fix.
- For service changes, verify with tests, health endpoints, logs, or container status as appropriate.
- Finish with what changed, what was verified, and any remaining risk.

## Definition of Done
A task is complete only when the requested outcome exists, the smallest reasonable change was made, relevant validation ran or the blocker is explicit, and production risk is visible to the user.
