# Copilot Operator Goal Templates

## 1. Bug fix

```text
Fix [bug name] in [area].

Constraints:
- Keep the fix minimal and localized.
- Do not change public API unless required.
- Add or update tests close to the behavior gap if needed.

Stop only when:
- relevant tests pass
- lint passes
- no high or critical blockers remain
- score >= 85
```

## 2. Feature implementation

```text
Implement [feature] in [area].

Constraints:
- Follow existing architecture and naming patterns.
- Prefer incremental changes over broad rewrites.
- Update tests and docs affected by the feature.

Stop only when:
- feature works in the intended path
- tests pass
- lint/build pass
- no high or critical blockers remain
- score >= 85
```

## 3. Refactor

```text
Refactor [module/file] to improve [goal].

Constraints:
- Preserve behavior unless a bug fix is explicitly requested.
- Keep the diff reviewable.
- Add safety tests if the change touches fragile logic.

Stop only when:
- tests pass
- lint/build pass
- behavior is preserved
- no high or critical blockers remain
- score >= 85
```

## 4. Audit / review

```text
Audit [area] for bugs, regressions, missing tests, and risky assumptions.

Constraints:
- Findings first, fixes second unless a fix is explicitly requested.
- Be strict about user-visible regressions and release blockers.

Stop only when:
- findings are clearly classified
- fixes are applied if requested
- validation has been rerun for touched areas
- score >= 85 or the session is blocked with a precise reason
```

## 5. Stabilization pass

```text
Stabilize the current branch.

Constraints:
- Prioritize failing tests, lint errors, type errors, and broken flows.
- Do not expand scope into unrelated enhancements.

Stop only when:
- tests pass
- lint/type/build pass
- no high or critical blockers remain
- score >= 85
```

## 6. Prompt checklist truoc khi run

- [ ] Goal noi ro pham vi
- [ ] Constraints noi ro dieu khong duoc pha
- [ ] Stop conditions ro rang
- [ ] Validation commands da co trong config
- [ ] Repo dang mo dung workspace
