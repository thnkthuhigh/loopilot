# Validation Map

## Active validation lanes

| Lane | Command | Required | Phase | Timeout |
|------|---------|----------|-------|---------|
| `tests` | `npm test` (5 Node + 23 Python) | **yes** | before + after | 120s |
| `lint` | `npm run lint` (ruff check) | **yes** | after only | 30s |

## Validation lanes (not yet active)

- `build`: no compile/build step for this project currently
- `smoke`: not yet defined — would be a lightweight path verification

## How it works

- **before_prompt**: operator runs configured validations before sending the next prompt to Copilot. Establishes a baseline. Currently: tests only.
- **after_response**: operator runs validations after Copilot finishes a turn. Results feed into the stop gate. Currently: tests + lint.
- A validation with `required: true` must pass for the stop gate to grant `STOP_GATE_PASSED`. If it fails, the operator reopens with `VALIDATION_FAILED`.

## Rollout order for new repos

1. Add `tests` (behavior correctness — the most important gate)
2. Add `lint` (code quality — catches regressions in style/imports)
3. Add `build` (integration/compile — proves the artifact is shippable)
4. Add `smoke` (lightweight end-to-end path verification)
