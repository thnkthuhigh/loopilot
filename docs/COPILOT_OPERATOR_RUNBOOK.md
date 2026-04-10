# Copilot Operator Runbook

## 1. Muc dich

Tai lieu nay dung de van hanh operator tren repo that ma khong can nho tung lenh va khong can ngam JSON bang tay moi lan.

## 2. Lenh quan trong

```bash
npm run operator:init -- --workspace C:\path\to\repo
npm run operator:doctor
npm run operator:status
npm run operator:plan
npm run operator:focus
npm run operator:watch -- --interval 2 --clear
npm run operator:cleanup -- --dry-run --keep-runs 10
npm run operator:run -- --goal-profile bug --goal "..."
npm run operator:resume
python -m copilot_operator run --goal-file .\goal.txt --goal-profile feature
npm test
npm run supervisor:doctor
```

## 3. Chuan bi truoc khi chay

### 3.1 Repo

- Repo dang mo trong VS Code
- Repo build duoc bang tay
- Test/lint/build command da biet ro, hoac de operator auto-detect truoc roi xac nhan lai
- Ban co quyen sua file trong repo do

### 3.2 VS Code

- `code.cmd` co tren PATH
- VS Code dang reuse dung window cua repo
- Copilot dang dang nhap va chay duoc o mode can dung
- Workspace nay resolve duoc `workspaceStorage`

### 3.2a Node.js (bat buoc cho hooks)

- Node.js >= 18 phai duoc cai va co tren PATH (`node --version` phai ra version)
- Neu Node.js thieu, hook `pre-tool-use.cjs` se khong chay duoc
- Khi hook khong chay, VS Code hien hop thoai "Allow?" moi lan agent dung tool
- Kiem tra: `node --version` trong terminal
- Neu van bi hoi "Allow" du da cai Node.js, vao VS Code Chat → doi Permission level sang **Bypass Approvals** hoac **Autopilot** cho session hien tai

### 3.3 Config

File can xem la [copilot-operator.yml](/c:/Users/thnkthuhigh/emmeo/copilot-operator.yml).

Can quan tam:

- `workspace`
- `mode`
- `goalProfile`
- `targetScore`
- `codeCommandRetries`
- `logRetentionRuns`
- `validation.tests.command`
- `validation.lint.command`
- `validation.build.command`

Neu repo co profile rieng, xem them [.copilot-operator/repo-profile.yml](/c:/Users/thnkthuhigh/emmeo/.copilot-operator/repo-profile.yml).

## 4. Checklist Day 0 cho repo moi

- [ ] Chay `npm run operator:init -- --workspace <repo>` neu repo do chua co scaffold
- [ ] Chay `npm run operator:doctor`
- [ ] Xem workspace detection co dung package manager khong
- [ ] Neu operator infer duoc `tests/lint/build`, xac nhan lai bang tay
- [ ] Chinh `.copilot-operator/repo-profile.yml`
- [ ] Chinh `copilot-operator.yml` neu can override
- [ ] Chay lai `doctor`
- [ ] Chay 1 smoke goal khong sua file voi `--goal-profile audit` hoac `default`
- [ ] Xac nhan `status/watch` hien duoc milestone plan va task queue
- [ ] Chay `npm run operator:watch -- --count 1`
- [ ] Doc `.copilot-operator/state.json`
- [ ] Doc `.copilot-operator/memory.md`
- [ ] Doc `.copilot-operator/session-summary.json`
- [ ] Xac nhan log iteration duoc ghi vao `.copilot-operator/logs/<runId>/`

## 5. Chon goal profile dung cach

- `bug`: sua loi, uu tien patch nho va regression coverage
- `feature`: them hanh vi moi, uu tien thin slice + tests/docs lien quan
- `refactor`: giu hanh vi, uu tien diff reviewable va checks chat
- `audit`: uu tien findings, severity, evidence, han che edit file
- `docs`: uu tien do chinh xac va vi du sat code that
- `default`: mode tong quat

## 6. Checklist viet goal dung cach

Goal tot can co 4 phan:

1. Muc tieu cu the
2. Gioi han / constraints
3. Gate can dat
4. Dieu kien dung

Mau goal:

```text
Fix loi pagination A4 trong preview.

Constraints:
- Khong doi API cong khai neu khong can.
- Uu tien sua nho gon dung cho luong hien tai.
- Neu can them test thi them test o vi tri sat behavior gap.

Stop only when:
- test pass
- lint pass
- khong con blocker high/critical
- score >= 85
```

## 7. Checklist cho moi lan chay

- [ ] Goal da ro va co stop condition
- [ ] Goal profile da chon dung
- [ ] Validation da dien dung hoac infer dung
- [ ] Repo dang mo dung workspace
- [ ] Khong co thay doi thu cong dang lam do ma operator co the dap len
- [ ] Co du disk / memory de VS Code va test chay
- [ ] Bat dau run operator
- [ ] Mo them 1 terminal va chay `npm run operator:watch -- --interval 2 --clear` neu muon theo doi live

## 8. Trong luc operator dang chay

Theo doi nhanh:

```bash
npm run operator:status
npm run operator:plan
npm run operator:focus
npm run operator:watch -- --interval 2 --clear
```

Neu can debug sau hon, xem:

- `.copilot-operator/state.json`
- `.copilot-operator/memory.md`
- `.copilot-operator/session-summary.json`
- `.copilot-operator/logs/<runId>/iteration-XX-prompt.md`
- `.copilot-operator/logs/<runId>/iteration-XX-response.md`
- `.copilot-operator/logs/<runId>/iteration-XX-decision.json`
- `.copilot-operator/logs/<runId>/iteration-XX-validation.before.json`
- `.copilot-operator/logs/<runId>/iteration-XX-validation.after.json`

Doc nhanh:

- `status=running`: dang chay
- `status=complete`: qua gate
- `status=blocked`: can nguoi can thiep
- `currentMilestoneId`: operator dang o milestone nao
- `currentTaskId`: operator dang day task nao trong milestone hien tai
- `decisionNextPrompt`: baton ma operator se gui tiep, co the duoc suy ra tu milestone/task queue
- `pendingDecision.reasonCode`: ly do operator tiep tuc
- `finalReasonCode`: ly do operator dung

## 9. Khi operator dung o trang thai complete

- [ ] Doc `reason` va `reasonCode` trong `session-summary.json` hoac `state.json`
- [ ] Kiem tra lai diff code
- [ ] Chay lai test/lint/build neu task quan trong
- [ ] Neu on, moi tien hanh commit / PR / merge theo quy trinh rieng

## 10. Khi operator dung o trang thai blocked

### 10.1 Cach chan doan

- Chay `npm run operator:status`
- Neu can, chay `npm run operator:watch -- --count 1`
- Doc `.copilot-operator/state.json`
- Doc response cua iteration cuoi
- Xem `pendingDecision`, `nextPrompt`, `reasonCode`
- Xem validation nao fail
- Xem Copilot co khong tra ve block may-doc hay khong

### 10.2 Nguyen nhan pho bien

#### A. `code chat` khong gui duoc

Dau hieu:
- `doctor` fail
- khong sinh them session file moi

Xu ly:
- dong/mo lai VS Code
- kiem tra `code.cmd --version`
- kiem tra dang mo dung repo
- tang `codeCommandRetries` neu fail dang ngan quang

#### B. Session tao ra nhung khong xong

Dau hieu:
- session file co update nhung khong co `completedAt`

Xu ly:
- tang `sessionTimeoutSeconds`
- kiem tra Copilot co dang cho xac nhan gi trong UI khong
- giam do lon cua goal

#### C. Copilot xong nhung khong tra ve block may-doc

Dau hieu:
- response text co noi da xong nhung operator van block

Xu ly:
- nhin response cuoi trong logs
- sua prompt template
- fallback parser da ho tro `SUPERVISOR_AUDIT`, nhung van nen uu tien `OPERATOR_STATE`

#### D. Validation fail

Dau hieu:
- Copilot bao done nhung operator van continue

Xu ly:
- doc command fail trong validation artifact
- xem output command
- dua output nay vao goal sau neu can

## 11. Planner va milestone

- Operator se co `plan` trong `state.json` va `session-summary.json`
- Moi milestone co the mang task queue rieng va operator se luu `currentTaskId` / `nextTaskId`
- Neu Copilot tra `next_prompt` mo ho, operator se doi sang baton dua tren task hien tai trong milestone active
- Copilot duoc yeu cau tra them `<OPERATOR_PLAN>` ben canh `<OPERATOR_STATE>`
- `status` va `watch` se hien `planSummary`, `currentMilestoneId`, `currentTaskId`, va danh sach milestone/task
- Neu plan ngu, operator van co fallback plan 1 milestone + 1 task de khong bi mat baton

## 12. Bao tri log va runtime

- Dung `logRetentionRuns` de gioi han so run dir duoc giu lai
- Dung `npm run operator:cleanup -- --dry-run --keep-runs 10` de preview cleanup
- Log kieu cu nam o root `.copilot-operator/logs/` co the giu tam de doi chieu lich su

## 13. Checklist hieu chinh hang tuan

- [ ] Review 5 session gan nhat
- [ ] Dem session complete vs blocked
- [ ] Dem ly do blocked pho bien nhat qua `reasonCode`
- [ ] Chinh prompt contract neu Copilot lap lai loi cu
- [ ] Chinh validation gate neu qua long hoac qua chat
- [ ] Ghi them known traps vao docs repo

## 14. Checklist truoc khi goi la production-ready

- [x] 3-5 task that lien tiep chay on
- [x] Chi phi model chap nhan duoc
- [x] Khong con fail ngau nhien do session parser
- [x] Memory va logs du de debug
- [ ] Team khac co the doc runbook va chay lai

## 15. Troubleshooting — Loi da gap va cach xu ly (E2E thuc chien)

### E. Chat session timeout — operator crash

**Dau hieu:**
- Log ghi `VSCodeChatError: ... timed out`
- Operator coi nhu fatal error va dung han

**Nguyen nhan:** Truoc day timeout duoc throw nhu non-retryable error.

**Xu ly (da fix trong v2.4.0):**
- Timeout gio la `VSCodeChatRetryableError` — operator tu retry
- Neu van gap, tang `sessionTimeoutSeconds` trong config
- Kiem tra VS Code khong bi lag/freeze

### F. Chat dir sai (chatSessions vs transcripts)

**Dau hieu:**
- Operator ghi `No chat session files found`
- Nhung Copilot Chat van co phien lam viec

**Nguyen nhan:** VS Code/Copilot Chat 0.42+ doi folder tu `chatSessions/` sang `sessions/` hoac `transcripts/`.

**Xu ly (da fix):**
- Operator gio auto-detect 3 paths: `chatSessions/`, `sessions/`, `transcripts/`
- Neu van loi, xem `workspaceStorage` path trong VS Code Developer Tools

### G. code chat --reuse-window gui vao window sai

**Dau hieu:**
- Prompt gui thanh cong nhung khong thay Copilot lam gi tren repo hien tai
- Chat hien thi o VS Code window khac

**Xu ly (da fix):**
- Operator gio chay `focus_workspace()` truoc moi `code chat` call
- Neu van sai, dam bao chi mo 1 VS Code window cho repo muc tieu

### H. Session summary khong ghi khi fatal error

**Dau hieu:**
- Run bi crash nhung khong co `session-summary.json`

**Xu ly (da fix):**
- Summary gio duoc ghi o tat ca error paths (try/except/finally)
- Resume van hoat dong binh thuong

### I. Windows encoding crash (cp1252)

**Dau hieu:**
- `UnicodeDecodeError` hoac `UnicodeEncodeError` khi output chua ky tu dac biet

**Xu ly (da fix):**
- Validation subprocess gio dung bytes mode + `decode('utf-8', errors='replace')`
- Terminal output dung `PYTHONIOENCODING=utf-8`

### J. KeyError 'kind' — event format loi

**Dau hieu:**
- `KeyError: 'kind'` khi doc session file

**Nguyen nhan:** Copilot Chat 0.42+ doi format transcript hoan toan — dung `type/data` thay `kind/value`.

**Xu ly (da fix):**
- Session store gio co `_load_event_log_session()` ho tro ca 2 format
- Events thieu `kind` duoc skip an toan

### K. Copilot Chat transcript format doi lien tuc

**Dau hieu:**
- Operator doc duoc response nhung parse sai
- Score luon = 0 du Copilot tra loi tot

**Xu ly:**
- Chay `copilot-operator doctor` de kiem tra version Copilot Chat
- Update operator len version moi nhat
- Neu gap format moi, bao loi de cap nhat session parser

### L. Operator chay 2 lan cung luc tren 1 workspace

**Dau hieu:**
- File state bi ghi de
- Ket qua khong nhat quan

**Xu ly (da fix trong v2.4.0):**
- Runtime guard (`runtime_guard.py`) dung lock file de ngan chay 2 operator
- Neu lock bi stale (process crash), operator tu take over sau 600s
- Conflict detection canh bao khi VS Code window dang co session active

### M. Operator loop vo han (khong tien bo)

**Dau hieu:**
- Copilot tra ve cung code moi iteration
- Score khong tang

**Xu ly (da fix trong v2.4.0):**
- Stop controller (`stop_controller.py`) tu dung sau N iterations khong co diff moi
- Diff dedup phat hien Copilot lap code cu
- Score floor hard stop neu score duoi 20 lien tuc 3 iterations
