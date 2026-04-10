# Copilot Operator Master Plan

## 1. Muc tieu toi thuong

Xay mot lop `external operator` dung ngoai GitHub Copilot de thay nguoi van hanh moi vong lam viec:

- giao muc tieu
- theo doi session
- doc ket qua that tu VS Code local storage
- quyet dinh co nen goi tiep hay dung
- ap stop gate truoc khi cho phep ket thuc
- dan den mot he thong co the chay tren repo that, backlog that, va CI that

Dich den cuoi cung khong phai la click-bot. Dich den cuoi cung la `autonomous delivery layer` nam tren Copilot.

## 2. Trang thai hien tai

Nhung gi da co trong workspace nay:

- [x] Operator goi Copilot qua `code chat --reuse-window --mode agent`
- [x] Operator tu resolve dung `workspaceStorage` cua repo dang mo
- [x] Operator tu doc `chatSessions/*.jsonl` de lay ket qua that
- [x] Operator hieu duoc `OPERATOR_STATE`
- [x] Operator fallback duoc `SUPERVISOR_AUDIT`
- [x] Operator co memory file va state file rieng
- [x] Operator co stop gate co ban dua tren `status`, `score`, `blockers`, `validation`
- [x] Co `doctor` command de kiem tra duong noi VS Code
- [x] Co smoke test thuc te voi `ask` mode va `agent` mode
- [x] Co test parser va decision loop bang Python

Gioi han hien tai:

- [x] Chua co profile cho repo that → **Done (Phase 2, repo-profile.yml)**
- [x] Chua co validation command that trong config → **Done (Phase 1, validation pipeline)**
- [x] Chua co planner dai han theo milestone → **Done (Phase 3, planner.py)**
- [x] Chua co multi-session orchestration → **Done (Phase 4, scheduler.py)**
- [x] Chua co git/CI/PR automation → **Done (Phase 5/6, repo_ops.py + ci_integration.py)**
- [x] Chua co dashboard va queue goal → **Done (Phase 7, dashboard.py + nightly.py)**
- [ ] Chua co UI automation fallback khi CLI khong du → **Hoãn (CLI đủ ~95% case)**

## 3. Kien truc muc tieu

He thong muc tieu gom 6 lop:

1. `Copilot Brain`
   Copilot trong VS Code van la thang code chinh.

2. `Operator Loop`
   Vong lap observe -> decide -> command -> verify -> continue.

3. `Project Brain`
   Nho dai han theo repo: coding standards, architecture map, test map, release gate, benchmark, known traps.

4. `Execution Rails`
   Validation, git, CI, issue tracker, changelog, release gate.

5. `Control Plane`
   Goal queue, policy, schedule, notifications, approvals.

6. `Studio Surface`
   Dashboard quan sat va can thiep khi can.

## 4. North Star

North star cua he thong:

> Ban chi can noi muc tieu, con operator tu dieu phoi Copilot lam cho den khi dat dinh nghia done da khai bao, hoac dung lai voi ly do block ro rang.

## 5. Phase roadmap

### Phase 1: Production Operator

Muc tieu:
Dua ban hien tai thanh cong cu co the dung hang ngay tren repo that.

Deliverables:

- config profile theo repo
- validation that: test, lint, build, smoke
- prompt contract on dinh cho task coding
- stop gate ro rang hon
- retry va resume session
- log va memory de debug duoc
- docs van hanh day du

Gate pass:

- chay on dinh tren it nhat 3 session lien tiep trong 1 repo that
- operator tu goi it nhat 2-3 vong ma khong can click tay
- khi fail thi de lai `state.json`, `memory.md`, `logs/*` de debug duoc

### Phase 2: Project Brain

Muc tieu:
Khong chi biet goal hien tai, ma biet ca repo dang lam gi.

Deliverables:

- `repo-profile.yml`
- `definition-of-done.md`
- `architecture-map.md`
- `validation-map.md`
- `benchmark-map.md`
- known traps va recovery playbook

Gate pass:

- prompt dau vao ngan hon nhung chat luong baton tot hon
- operator khong can nhac lai cac rule co dinh trong moi goal
- giam so vong lap vi Copilot duoc cap context dung hon

### Phase 3: Autonomous Planner

Muc tieu:
Tu pha 1 goal lon thanh chuoi task nho co thu tu.

Deliverables:

- planner chia backlog theo milestone
- objective tree
- priority engine
- tie-break rule khi co nhieu viec cung gap
- progress scoring theo milestone thay vi chi theo 1 turn

Gate pass:

- nhan 1 epic lon va tu chia thanh 3-10 step hop ly
- moi vong tiep theo dua tren milestone, khong chi dua tren text free-form

### Phase 4: Multi-Session Copilot Farm

Muc tieu:
Mot operator dieu phoi nhieu session Copilot song song.

Deliverables:

- session role: implement, test, audit, docs
- scheduler tranh ghi de len nhau
- merge baton giua nhieu session
- conflict detector

Gate pass:

- chay duoc it nhat 2 session song song tren write-scope tach biet
- gom ket qua vao mot stop gate chung

### Phase 5: UI Driver Fallback

Muc tieu:
Neu CLI khong cover het, co them tay dieu khien UI nhu nguoi.

Deliverables:

- UI automation cho VS Code panel
- attach file / paste prompt / click continue / xu ly popup
- selector registry hoac image fallback
- watchdog khi UI thay doi

Gate pass:

- van hanh duoc ca nhung thao tac khong expose qua `code chat`
- co fallback mode ma khong pha mode CLI chinh

### Phase 6: Repo Ops

Muc tieu:
Day operator tu mức chat orchestration len muc giao hang ky thuat.

Deliverables:

- tao branch
- checkpoint commit
- chay test matrix
- doc CI fail va goi Copilot sua tiep
- tao PR draft khi dat gate
- changelog / summary / rollout note

Gate pass:

- 1 task nho co the di tu goal -> code -> test -> commit -> draft PR

### Phase 7: Delivery System

Muc tieu:
Noi operator voi backlog that va su kien that.

Deliverables:

- doc Linear/Jira/GitHub Issues
- queue goal theo priority
- nightly run
- alert khi blocked lau
- handoff report cuoi ngay

Gate pass:

- mot ticket co the duoc keo vao, xu ly, cap nhat trang thai, va ban nhan duoc report ma khong can canh tay

### Phase 8: AI Engineering Studio

Muc tieu:
Bien he thong thanh mot xưởng dieu phoi AI coding.

Deliverables:

- dashboard live
- queue, SLA, chi phi model, success rate
- per-repo profile
- policy va approval lane
- heatmap blocker va quality trend
- playbook deployment

Gate pass:

- van hanh duoc nhieu repo
- co metric thuc de biet ROI
- co vai tro ro rang giua nguoi va operator

## 6. Nguyen tac thiet ke bat buoc

- Copilot van la coding brain, khong patch truc tiep vao extension
- Operator phai co the debug bang log va state file
- Moi quyet dinh stop phai co ly do may-doc va nguoi-doc deu hieu
- Stop gate la trung tam, khong duoc de "done" theo cam tinh
- Moi pha phai dat duoc 1 outcome co the dem bang metric
- Neu co CLI chinh thuc thi uu tien CLI truoc UI automation
- Neu co duong local storage chac chan thi uu tien doc su that tu file truoc OCR / screenshot

## 7. KPI muc tieu

### KPI ky thuat

- autonomous turn completion rate
- average iterations per task
- percentage session can stop without human intervention
- validation pass rate on first stop attempt
- blocked session recovery rate
- mean time to operator diagnosis

### KPI san pham noi bo

- so phut babysit tiet kiem moi task
- ti le task co the giao goal mot lan roi de no tu chay
- ti le task dat chat luong dung ngay lan stop dau
- chi phi model / task so voi thoi gian tiet kiem

## 8. Thu tu uu tien de lam dung

Thu tu nen theo la:

1. Production Operator
2. Project Brain
3. Autonomous Planner
4. Repo Ops
5. Multi-Session
6. Delivery System
7. UI Driver
8. Studio Surface

Ly do:

- neu operator don chua on dinh thi scale len se vo
- neu khong co project brain thi planner se noi chung chung
- neu chua co stop gate va validation that thi multi-session se tang toc do sai

## 9. Dinh nghia thanh cong sau 90 ngay

Sau 90 ngay, he thong duoc xem la dang vao guong dung neu:

- ban co the giao mot goal coding ro rang cho repo that
- operator tu chay 2-5 vong voi Copilot ma khong can click tay
- operator tu chay test/lint/build va biet ly do fail
- operator chi dung khi qua gate hoac blocked ro ly do
- moi session de lai memory, state, logs du de debug
- ban co checklist va runbook de giao cho nguoi khac trong team dung chung
