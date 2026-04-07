# Architecture Map

## Runtime layers

- `copilot_operator/config.py`: loads base config (copilot-operator.yml), merges repo profile (.copilot-operator/repo-profile.yml), detects workspace ecosystem, infers validation commands
- `copilot_operator/repo_inspector.py`: auto-detects ecosystem (Node/Python/mixed), package manager, infers test/lint/build commands from project files
- `copilot_operator/vscode_chat.py`: speaks to VS Code through `code chat --mode agent` and waits for session files in workspaceStorage
- `copilot_operator/session_store.py`: reconstructs session state from `.jsonl` incremental events or `.json` snapshots
- `copilot_operator/prompts.py`: renders operator prompts (initial + follow-up), parses `<OPERATOR_STATE>` and `<OPERATOR_PLAN>` blocks, falls back to `<SUPERVISOR_AUDIT>`
- `copilot_operator/planner.py`: milestone/task queue parsing, state merging, baton generation, plan rendering — the core planning engine
- `copilot_operator/operator.py`: main observe → decide → command → verify loop, stop gate logic, resume support, artifact logging
- `copilot_operator/validation.py`: runs operator-side validations (tests/lint/build) before and after each Copilot turn
- `copilot_operator/bootstrap.py`: workspace init, cleanup, status/focus/watch rendering
- `copilot_operator/cli.py`: CLI dispatcher — doctor, init, run, resume, status, plan, focus, watch, cleanup

## Runtime artifacts

- `.copilot-operator/memory.md`: rolling 5-iteration continuity + milestone status, attached to every Copilot prompt
- `.copilot-operator/state.json`: full machine state for resume/debug (runId, goal, plan, history, pendingDecision)
- `.copilot-operator/session-summary.json`: concise result of the latest run (status, reason, reasonCode, score, iterations, plan)
- `.copilot-operator/logs/<runId>/`: per-iteration artifacts (prompt.md, response.md, decision.json, validation.before.json, validation.after.json)

## Key data flow

```
goal + config → initial prompt → code chat → session file → parse OPERATOR_STATE + OPERATOR_PLAN
  → merge plan → run validation → decide (gate logic) → baton → follow-up prompt → repeat
```

## Compatibility layer

- `.github/`: earlier internal supervisor scaffold still present, so prompts must tolerate `SUPERVISOR_AUDIT` in addition to `OPERATOR_STATE`
