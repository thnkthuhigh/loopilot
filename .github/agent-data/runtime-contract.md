# Supervisor Runtime Contract

## State File

- Path: `.copilot/supervisor/<sessionId>.json`
- Required fields:
  - `iteration`
  - `score`
  - `previousScore`
  - `forcedContinuations`
  - `gaps`
  - `blockers`
  - `nextDirective`
  - `lastBenchmarkAt`

The scaffold also keeps a small `checks` object and a `meta` object for plateau tracking, files touched, and command history.

## Tagged Reports

### Researcher

```
<SUPERVISOR_RESEARCH>{"repoPatterns":[],"marketFindings":[],"gaps":[],"recommendedFocus":""}</SUPERVISOR_RESEARCH>
```

### Implementer

```
<SUPERVISOR_IMPLEMENTATION>{"summary":"","filesTouched":[],"checksRun":[],"openRisks":[]}</SUPERVISOR_IMPLEMENTATION>
```

### Auditor

```
<SUPERVISOR_AUDIT>{"score":0,"readyToStop":false,"summary":"","nextDirective":"","checks":{"tests":{"status":"unknown","evidence":""},"lint":{"status":"unknown","evidence":""},"benchmark":{"status":"unknown","evidence":""}},"blockers":[],"gaps":[]}</SUPERVISOR_AUDIT>
```

## Severity Vocabulary

- `critical`
- `high`
- `medium`
- `low`
- `info`

## Stop Hook Rule

The `Supervisor` agent can only stop when all of the following are true:

- Audit score is at or above the configured target.
- Tests are passing.
- Lint is passing.
- No `critical` or `high` blockers remain.
- Auditor explicitly marks `readyToStop: true`, or all stop conditions are otherwise satisfied.
