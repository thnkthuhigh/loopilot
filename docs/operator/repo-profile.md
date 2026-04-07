# Repo Profile

## Identity

- Repo name: emmeo
- Primary purpose: autonomous Copilot Operator — an external orchestration loop that drives GitHub Copilot in VS Code, parses session artifacts, evaluates stop gates, and decides continue-or-stop without manual intervention.

## Architecture summary

- `copilot_operator/`: Python runtime — operator loop, milestone/task planner, prompt engine, config loader, validation runner, repo inspector, session parser, CLI
- `.copilot-operator/`: runtime state (state.json, memory.md, session-summary.json), repo-profile.yml, per-run logs
- `docs/`: roadmap (MASTER_PLAN), backlog (BACKLOG), checklist, goal templates, runbook
- `docs/operator/`: project brain — architecture map, definition of done, known traps, validation map, repo profile
- `tests/`: 5 Node.js hook tests (hooks.test.cjs)
- `tests_python/`: 23 Python unit tests (test_operator.py) covering session parsing, planner, config, validation, decision logic, bootstrap, prompts
- `.github/`: legacy internal supervisor scaffold (hooks, agents) — kept for compatibility
- Stack: Python 3.10+, Node.js, ruff (lint), unittest + node:test

## Current focus

- Phase 1 production hardening complete — operator is milestone + task-queue aware
- Next: run 3–5 sustained autonomous sessions on real tasks without manual intervention
- Then: Phase 2 Project Brain enrichment, Phase 3 Autonomous Planner
