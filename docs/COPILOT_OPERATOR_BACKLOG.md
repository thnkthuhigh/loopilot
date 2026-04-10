# Copilot Operator Backlog

> **Cập nhật:** v2.5.0 — hầu hết items đã hoàn thành.

## Cach dung backlog nay

Moi item duoi day la ticket-ready o muc ky thuat. Ban co the copy nguyen khoi sang issue tracker.

## P0

### OP-001: Repo profile support ✅

Muc tieu:
Cho operator nap profile rieng theo repo thay vi chi dua vao `copilot-operator.yml` chung.

Output:

- `repo-profile.yml`
- loader trong operator
- merge rule giua global config va repo config

Done khi:

- moi repo co the co stop gate, prompt, command, path rule rieng

**→ Hoàn thành Phase 2. Đã chạy trên note-cli + english.**

### OP-002: Validation pipeline that ✅

Muc tieu:
Cho operator hieu `tests`, `lint`, `build`, `smoke` nhu cac gate doc lap.

Output:

- validation schema mo rong
- command timeout rieng
- status tong hop

Done khi:

- operator biet ly do fail cua tung gate va ghi lai vao state

**→ Hoàn thành Phase 1. Jest, Vitest, ESLint, pytest, ruff đều đã cấu hình.**

### OP-003: Resume session ✅

Muc tieu:
Neu operator bi dung giua chung, co the resume tu `state.json` thay vi bat dau lai.

Output:

- `resume` command
- logic nap history va next baton

Done khi:

- 1 run dang do co the tiep tuc ma khong mat memory

**→ Hoàn thành. CLI `copilot-operator resume` hoạt động.**

### OP-004: Structured decision log ✅

Muc tieu:
Tach ro prompt, response, decision, validation thanh cac artifact de debug.

Output:

- `decision.json`
- `validation.json`
- summary file cuoi session

Done khi:

- moi ly do continue/stop deu truy nguon duoc

**→ Hoàn thành. Ghi decision.json + validation.json + summary per run.**

## P1

### OP-005: Goal templates ✅

Muc tieu:
Co mau goal cho bug, feature, refactor, audit, docs.

Output:

- `docs/COPILOT_OPERATOR_GOAL_TEMPLATES.md`
- profile prompt theo task type

Done khi:

- nguoi dung khong can tu nghi prompt tu dau cho moi task

**→ Hoàn thành. 6 goal templates + goalProfile trong config.**

### OP-006: Project brain seed ✅

Muc tieu:
Tao bo nho co cau truc cho repo.

Output:

- `architecture-map.md`
- `definition-of-done.md`
- `validation-map.md`
- `known-traps.md`

Done khi:

- prompt moi co the attach memory dung theo repo

**→ Hoàn thành Phase 2. Đầy đủ 4 file trong docs/operator/.**

### OP-007: Better blocker classification ✅

Muc tieu:
Phan loai blocker co he thong hon.

Output:

- severity rubric
- reason code
- blocker source: copilot, validation, operator, environment

Done khi:

- `blocked` khong con mo ho

**→ Hoàn thành. Severity rubric + reason code trong benchmark.py.**

## P2

### OP-008: Autonomous planner ✅

Muc tieu:
Chia epic thanh chuoi task nho.

Output:

- planning module
- milestone state
- task queue nho

Done khi:

- 1 goal lon duoc chia ra va thuc thi tung phan

**→ Hoàn thành Phase 3. planner.py + goal_decomposer.py.**

### OP-009: Multi-session scheduler ✅

Muc tieu:
Chay nhieu session Copilot song song co dieu phoi.

Output:

- scheduler
- role assignment
- file ownership map

Done khi:

- 2 session co the cung xu ly 1 task lon ma khong dap len nhau

**→ Code hoàn thành Phase 4. Chưa test thực chiến song song.**

### OP-010: Git and PR rails ✅

Muc tieu:
Them branch, checkpoint commit, draft PR vao pipeline.

Output:

- branch strategy
- commit formatter
- PR summary generator

Done khi:

- task nho di duoc tu goal den draft PR

**→ Hoàn thành. repo_ops.py: branch, commit, PR, changelog.**

## P3

### OP-011: Issue tracker integration ✅

Muc tieu:
Cho operator keo task tu Linear/Jira/GitHub Issues.

Output:

- issue sync
- status update
- comment handoff

Done khi:

- operator co the nhan ticket that va tra ticket that

**→ Hoàn thành Phase 6. github_integration.py: issues, PRs, labels.**

### OP-012: UI fallback driver ⏸️ HOÃN

Muc tieu:
Neu CLI gap gioi han thi co UI automation fallback.

Output:

- UI action layer
- selector/image registry
- watchdog

Done khi:

- operator van tiep tuc duoc khi CLI khong du

**→ Hoãn. CLI đã xử lý ~95% use case. Dễ nợ kỹ thuật.**

### OP-013: Dashboard ✅

Muc tieu:
Hien thi live state cua operator va session.

Output:

- queue view
- session view
- blocker view
- cost view

Done khi:

- khong can mo file raw de biet he thong dang ra sao

**→ Hoàn thành Phase 7. dashboard.py: TUI real-time.**

## P4

### OP-014: Nightly delivery loop ✅

Muc tieu:
Cho phep queue task chay ban dem va sang co report.

**→ Hoàn thành. nightly.py.**

### OP-015: Policy engine ✅

Muc tieu:
Action nguy hiem phai qua approval lane.

**→ Hoàn thành. policy.py: rules, cost ceilings, approval lanes.**

### OP-016: ROI analytics ✅

Muc tieu:
Do duoc operator tiet kiem bao nhieu thoi gian va tien.

**→ Hoàn thành. roi.py: success rate, cost/task, time saved.**

---

## Tổng kết: 15/16 hoàn thành, 1 hoãn (OP-012 UI fallback)
