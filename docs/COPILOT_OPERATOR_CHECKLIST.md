# Copilot Operator Master Checklist

## A. Baseline da co trong workspace nay

- [x] Co `copilot_operator` package de chay operator ben ngoai Copilot
- [x] Co `doctor` command: `npm run operator:doctor`
- [x] Co `run` command: `npm run operator:run -- --goal "..."`
- [x] Co `resume` command: `npm run operator:resume`
- [x] Co `init` command: `npm run operator:init -- --workspace <repo>`
- [x] Co `status` command: `npm run operator:status`
- [x] Co `watch` command: `npm run operator:watch -- --interval 2 --clear`
- [x] Co `cleanup` command: `npm run operator:cleanup -- --dry-run --keep-runs 10`
- [x] Co parser cho `OPERATOR_STATE`
- [x] Co fallback parser cho `SUPERVISOR_AUDIT`
- [x] Co `memory.md`, `state.json`, `session-summary.json`, `logs/`
- [x] Co per-iteration artifacts cho prompt, response, decision, validation-before, validation-after
- [x] Co per-run artifact directory theo `runId`
- [x] Co `repo-profile.yml` va `repo-profile.example.yml`
- [x] Co project memory docs duoc attach vao session
- [x] Co auto-detect ecosystem/package manager
- [x] Co inferred validation commands khi config de trong
- [x] Co goal profiles: `default`, `bug`, `feature`, `refactor`, `audit`, `docs`
- [x] Co milestone planner state qua `<OPERATOR_PLAN>`
- [x] Co task queue state ben trong milestone active
- [x] Co milestone/task-driven baton khi `next_prompt` qua mo ho
- [x] Co reason codes cho stop/continue decisions
- [x] Co test parser, decision loop, config merge, validation phase, resume bootstrap, cleanup, repo detection
- [x] Co smoke test voi `ask` mode
- [x] Co smoke test voi `agent` mode

## B. P0 checklist de dung tren repo that

### B1. Gan vao repo that

- [ ] Copy hoac move operator vao repo muc tieu
- [ ] Chinh `workspace` trong `copilot-operator.yml`
- [ ] Tao `.copilot-operator/repo-profile.yml` cho repo muc tieu
- [ ] Chay `npm run operator:doctor`
- [ ] Xac nhan VS Code mo dung repo muc tieu
- [ ] Xac nhan `workspaceStorage` resolve dung repo

### B2. Khai bao gate that

- [ ] Xem command operator infer ra co dung khong
- [ ] Dien command `tests` neu infer chua dung
- [ ] Dien command `lint` neu infer chua dung
- [ ] Dien command `build` neu infer chua dung
- [ ] Neu can, them `smoke`
- [ ] Danh dau command nao la `required: true`
- [ ] Chon command nao can `runBeforePrompt`
- [ ] Chay tung command bang tay 1 lan de xac nhan no thuc su dung

### B3. Goal contract

- [x] Co `default-goal.txt` mau
- [x] Co template goal trong `docs/COPILOT_OPERATOR_GOAL_TEMPLATES.md`
- [ ] Tao `goal.txt` mau rieng cho repo that
- [ ] Gan goal profile cho tung loai task that
- [ ] Them rule theo domain that cua repo
- [ ] Dinh nghia ro khi nao duoc goi la `done`

### B4. Stop gate thuc chien

- [ ] Dinh nghia score target cho repo
- [ ] Dinh nghia blocker severity nao la cam stop
- [ ] Dinh nghia khi nao `tests` co the la `not_applicable`
- [ ] Dinh nghia khi nao `lint` co the la `not_applicable`
- [ ] Dinh nghia khi nao cho phep `blocked`
- [ ] Chot bang reason code nao duoc phep stop that

### B5. Van hanh 3 session mau

- [ ] Chay 1 task bug fix nho
- [ ] Chay 1 task them test
- [ ] Chay 1 task sua lint/type error
- [ ] Luu lai memory/state/logs cua ca 3 task
- [ ] Tong ket operator gap cho nao nhieu nhat

## C. P1/P2 checklist da co mot phan va con lai can hoan tat

### C1. Resume, retry, retention

- [x] Co `resume` mode tu `state.json`
- [x] Co retry khi `code chat` fail tam thoi
- [ ] Co retry khi session file tao cham
- [x] Co timeout message ro rang cho tung loai fail
- [x] Co cleanup command cho old run directories
- [ ] Co cleanup session cu khi can

### C2. Quan sat va debug

- [x] Log them metadata quan trong cua tung iteration
- [x] Tach `prompt`, `response`, `decision`, `validation` thanh artifact rieng
- [x] Them `session-summary.json` cuoi moi session
- [x] Them `status` command de doc state nhanh
- [x] Them `watch` command de theo doi live
- [x] Co checklist doc log khi bi block
- [x] Co reason code de debug nhanh

### C3. Profile theo repo

- [x] Tao `repo-profile.yml`
- [x] Tao `repo-profile.example.yml`
- [x] Auto-detect package manager co ban
- [ ] Khai bao package manager that cho repo muc tieu neu auto-detect chua dung
- [ ] Khai bao command test/lint/build that
- [x] Khai bao path nhay cam co ban
- [x] Khai bao summary va standards co ban
- [ ] Khai bao output mong muon cho docs / tests / PR summary

### C4. Chat quality

- [x] Them repo profile context vao prompt
- [x] Them workspace insight vao prompt
- [x] Them goal profile guidance vao prompt
- [x] Day memory file toi uu hon bang recent history + artifacts
- [ ] Rut gon history chi giu thong tin can cho baton hon nua
- [ ] Theo doi vong nao hay lap lai vo ich

## D. P3 checklist de co Planner

### D1. Milestone planner co ban

- [x] Parse `<OPERATOR_PLAN>` block
- [x] Luu `plan` vao `state.json` va `session-summary.json`
- [x] Hien `planSummary`, `currentMilestoneId`, va `currentTaskId` trong `status/watch`
- [x] Co `plan` command de doc milestone rieng
- [x] Co fallback plan 1 milestone + 1 task khi Copilot chua tra plan
- [ ] Danh gia milestone/task completion tinh vi hon
- [x] Dung milestone de anh huong `nextPrompt` thay vi chi dua vao free text

### D2. Repo memory co cau truc

- [x] Tao `docs/operator/repo-profile.md`
- [x] Tao `docs/operator/architecture-map.md` thay bang noi dung trong `repo-profile.md`
- [x] Tao `docs/operator/definition-of-done.md`
- [x] Tao `docs/operator/validation-map.md`
- [x] Tao `docs/operator/known-traps.md`
- [ ] Chuyen bo nay sang repo that va dien noi dung that

### D3. Benchmark va quality memory

- [ ] Dinh nghia danh sach doi thu / benchmark can so
- [ ] Dinh nghia quality rubric theo loai task
- [ ] Dinh nghia checklist accessibility/security/performance neu co
- [ ] Dinh nghia "khong duoc goi la done neu..."

## E. P4 checklist de co Planner that

- [ ] Tu chia goal lon thanh milestones
- [ ] Tu chon milestone tiep theo dua tren gate hien tai
- [x] Tu luu progress theo milestone va task queue
- [x] Tu tao baton theo milestone/task thay vi text chung chung
- [ ] Tu danh dau blocked o muc milestone/task

## F. P5 checklist de co Repo Ops

- [ ] Tao branch theo convention
- [ ] Commit checkpoint theo milestone
- [ ] Chay CI local / remote
- [ ] Doc ket qua CI va mo vong fix tiep
- [ ] Tao draft PR summary
- [ ] Tao changelog / rollout note

## G. P6 checklist de co Multi-Session

- [ ] Session role: implement
- [ ] Session role: test
- [ ] Session role: audit
- [ ] Scheduler tranh xung dot file
- [ ] Merge baton tu nhieu session
- [ ] Stop gate chung cho ca task

## H. P7 checklist de co Delivery System

- [ ] Noi voi issue tracker
- [ ] Queue goal theo priority
- [ ] Co nightly run
- [ ] Co report cuoi ngay
- [ ] Co alert khi blocked lau
- [ ] Co SLA cho tung loai task

## I. P8 checklist de co Studio

- [ ] Dashboard session
- [ ] Dashboard chi phi model
- [ ] Dashboard success rate
- [ ] Dashboard blocker trend
- [ ] Dashboard per-repo health
- [ ] Approval lane cho action nguy hiem

## J. Definition of done cho phase hien tai

Phase hien tai duoc xem la xong khi:

- [ ] Operator chay duoc tren repo that cua ban
- [ ] Validation that da duoc dien vao config
- [ ] Co 3-5 prompt template that
- [ ] Co 3 session thanh cong lien tiep khong can click tay
- [ ] Co runbook de nguoi khac trong team doc va dung
