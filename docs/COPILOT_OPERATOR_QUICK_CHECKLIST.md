# Copilot Operator Quick Checklist

## Day 0

- [ ] Run `python -m copilot_operator doctor`
- [ ] Fill real commands into `copilot-operator.yml` or `.copilot-operator/repo-profile.yml`
- [ ] Fill `docs/operator/*` with project-specific truth
- [ ] Run one smoke goal that does not edit files
- [ ] Run one real bug-fix or lint-fix goal

## Before each run

- [ ] Goal is specific and has stop conditions
- [ ] Goal profile is chosen correctly (`bug`, `feature`, `refactor`, `audit`, `docs`, or `default`)
- [ ] VS Code is open on the correct repo
- [ ] Required validations are configured
- [ ] There are no risky uncommitted local edits that the operator could overwrite
