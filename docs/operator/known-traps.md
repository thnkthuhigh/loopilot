# Known Traps

## Prompt / Session
- Workspace `.github/copilot-instructions.md` may force Copilot to emit a machine-readable block different from `OPERATOR_STATE`. The operator accepts `SUPERVISOR_AUDIT` as a fallback.
- If `code chat` targets the wrong VS Code window, the session will appear in another workspaceStorage folder. Always verify `operator:doctor` output first.
- Copilot may return a generic `next_prompt` like "Continue" or "Resume work". The planner detects this via `is_generic_baton()` and substitutes a task-aware baton.

## Validation
- If required validation commands are left blank, the stop gate is too weak — Copilot can claim done without proof. Always fill real commands.
- If the repo has long-running tests, tune `sessionTimeoutSeconds` and `validation[].timeoutSeconds` separately — operator timeout ≠ validation timeout.
- `npm test` runs both Node and Python tests in sequence. A failure in either blocks the gate.

## Planning
- If Copilot omits `<OPERATOR_PLAN>`, operator creates a fallback single-milestone plan from the goal text. This is safe but loses task granularity.
- `merge_plan()` marks all milestones/tasks done when Copilot reports terminal `status=done`. If the score is below target, the operator reopens the run — but the plan stays marked done. Subsequent iterations get a fresh baton from the reopen reason.

## Resume
- Resuming a `complete` run is rejected. Only `blocked` or `running` states can be resumed.
- `resumeCount` increments each time — monitor it to detect loops.

## Environment
- Python 3.10+ required (dataclasses, walrus operator usage).
- `ruff` must be installed for lint validation to pass (`pip install ruff`).
