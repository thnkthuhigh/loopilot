# Local Copilot Supervisor

This workspace is configured for a local supervisor loop in VS Code agent mode.

## Always-On Rules

- Prefer the `Supervisor` custom agent for any multi-step implementation or self-directed improvement task.
- Never merge, deploy, publish, release, or push remote automation from this workspace.
- Never run destructive terminal commands such as `git reset --hard`, `git clean -fdx`, `rm -rf`, or recursive force deletes.
- End every meaningful implementation loop with a self-review and an Auditor pass.
- If the current work is not ready to stop, produce a concrete next directive instead of a vague closing message.

## Machine-Readable Checkpoints

Use these exact tags when the corresponding custom agent reports back:

- Researcher: `<SUPERVISOR_RESEARCH>{...}</SUPERVISOR_RESEARCH>`
- Implementer: `<SUPERVISOR_IMPLEMENTATION>{...}</SUPERVISOR_IMPLEMENTATION>`
- Auditor: `<SUPERVISOR_AUDIT>{...}</SUPERVISOR_AUDIT>`

Do not wrap those tags in Markdown code fences. Keep the JSON valid.

## Audit Expectations

- Stop only when the score reaches the configured target, tests and lint are passing, and there are no remaining `critical` or `high` blockers.
- If benchmark research is stale or missing, call that out explicitly in the audit report.
- When blocked, explain exactly what prevented further progress and what human input or repo change is needed next.
