# Loopilot — Level 2: Supervised Worker — Master Plan

> **Mục tiêu**: Hoàn thiện Level 2 — operator chạy đủ tốt để user chỉ cần nhìn kết quả cuối,
> không cần canh từng bước. Chỉ xuất hiện ở đúng checkpoint có giá trị.
>
> **Hiện tại**: v1.2.0 — Level 1 hoàn chỉnh, ~70% Level 2
>
> **Đích đến**: Level 2 vững ≥ 95% trước khi nghĩ tới Level 3

---

## Tổng quan các giai đoạn

| Giai đoạn | Tên | Mục tiêu chính | Effort |
|-----------|-----|----------------|--------|
| **Phase 1** | Validation Auto-Fill | `init` tự phát hiện và điền commands | Nhỏ |
| **Phase 2** | Auto-Resume | Tự detect pending state và tiếp tục | Nhỏ |
| **Phase 3** | Error Recovery Nâng Cao | Backoff, error-type handling, session reset | Trung bình |
| **Phase 4** | Risk Gate & Escalation | Protected path → escalate thay vì block | Trung bình |
| **Phase 5** | Session Context Optimization | Giảm lãng phí context khi tạo session mới | Trung bình |
| **Phase 6** | E2E Stress Test | Test goal phức tạp, multi-file, edge cases | Trung bình |
| **Phase 7** | Level 2 Graduation | Audit tổng, benchmark, tag stable | Nhỏ |

---

## Phase 1 — Validation Auto-Fill on Init

### Vấn đề
`initialize_workspace()` ghi `command: ""` cho tất cả validation. User phải tự sửa YAML.
`_apply_validation_hints()` chỉ chạy lúc runtime `load_config()`, không ghi lại vào file.

### Giải pháp
1. Sau khi `initialize_workspace()` ghi scaffold, gọi `detect_workspace_insight()`
2. Dùng hints để hydrate validation commands trong `copilot-operator.yml`
3. Ghi lại file YAML với commands đã điền
4. Nếu không detect được → giữ `command: ""` + comment hướng dẫn

### Checklist

- [ ] **P1.1** — Trong `bootstrap.py::initialize_workspace()`, sau khi ghi scaffold:
  - Gọi `detect_workspace_insight(workspace)`
  - Đọc lại `copilot-operator.yml` bằng `load_config()`
  - Ghi lại file với validation commands đã hydrate
- [ ] **P1.2** — Thêm comment trong YAML template cho trường hợp không detect được:
  ```yaml
  command: ""  # auto-detected: chưa tìm thấy; điền tay nếu cần
  ```
- [ ] **P1.3** — Thêm `--detect-hints` flag cho `init` command (mặc định bật)
- [ ] **P1.4** — Thêm CLI subcommand `detect-hints` chạy riêng (re-detect + update YAML)

### Test

- [ ] **T1.1** — `test_init_fills_validation_commands_for_node_project`:
  Tạo temp dir với `package.json` có scripts test/lint → init → đọc YAML → assert commands không rỗng
- [ ] **T1.2** — `test_init_fills_validation_commands_for_python_project`:
  Tạo temp dir với `pyproject.toml` + pytest → init → assert `pytest` trong command
- [ ] **T1.3** — `test_init_keeps_empty_when_no_ecosystem_detected`:
  Tạo temp dir trống → init → assert commands rỗng + comment hướng dẫn
- [ ] **T1.4** — `test_init_does_not_overwrite_existing_commands`:
  Tạo YAML có command đã điền → init → assert command giữ nguyên
- [ ] **T1.5** — `test_detect_hints_subcommand`:
  Chạy `detect-hints` → assert YAML được update

### Tiêu chí hoàn thành
- `init` trên Node project → YAML có `npm test`, `npm run lint`
- `init` trên Python project → YAML có `pytest`, `ruff check .`
- Không phá existing commands
- Lint + test pass

---

## Phase 2 — Auto-Resume

### Vấn đề
Sau khi run bị dừng (blocked/max-iterations/error), user phải chạy `resume` tay.
Nếu user chạy `run` lại với cùng goal, hệ không biết nên tiếp hay bắt đầu lại.

### Giải pháp
1. Khi `run` được gọi, kiểm tra `state.json` có `pendingDecision`
2. Nếu có pending decision với action `continue` → auto-resume
3. Thêm `--fresh` flag để ép bắt đầu lại (bỏ qua pending state)

### Checklist

- [ ] **P2.1** — Trong `cli.py::_run()`:
  - Đọc `state.json` nếu tồn tại
  - Nếu `status == 'blocked'` và `pendingDecision.action == 'continue'` → set `resume=True`
  - Print thông báo: `"Auto-resuming previous run (iteration X, score Y)..."`
- [ ] **P2.2** — Thêm `--fresh` flag cho `run` command:
  - `--fresh` → xóa state cũ, bắt đầu mới
  - Default: auto-detect resume
- [ ] **P2.3** — Trong `operator.py::run()`:
  - Khi resume, check nếu goal khác goal cũ → warn + confirm fresh
- [ ] **P2.4** — Khi run kết thúc với `status=blocked`, ghi rõ `pendingDecision` vào state

### Test

- [ ] **T2.1** — `test_auto_resume_when_pending_decision_exists`:
  Tạo state.json với pendingDecision → gọi run → assert resume path taken
- [ ] **T2.2** — `test_fresh_flag_ignores_pending_state`:
  Tạo state.json → gọi run --fresh → assert fresh start
- [ ] **T2.3** — `test_no_auto_resume_when_status_complete`:
  Tạo state.json với status=complete → gọi run → assert fresh start
- [ ] **T2.4** — `test_auto_resume_prints_notification`:
  Assert stdout chứa "Auto-resuming"
- [ ] **T2.5** — `test_warn_when_goal_changed_on_resume`:
  State có goal A, user chạy run với goal B → assert warning

### Tiêu chí hoàn thành
- Chạy `run` sau khi bị blocked → tự tiếp tục không cần `resume`
- `--fresh` ép bắt đầu lại
- Goal thay đổi → cảnh báo rõ ràng
- Lint + test pass

---

## Phase 3 — Error Recovery Nâng Cao

### Vấn đề
- Retry ngay lập tức, không backoff → có thể spam VS Code
- Không phân biệt loại error → retryable vs fatal xử lý giống nhau
- Không reset session khi liên tục fail

### Giải pháp
1. Exponential backoff: 2s, 4s, 8s giữa các retry
2. Phân loại error: retryable (VSCodeChatRetryableError, timeout) vs fatal (config error, missing binary)
3. Sau 2 consecutive retryable errors → reset session context (tạo session mới)
4. Log error type rõ ràng trong state.json

### Checklist

- [ ] **P3.1** — Thêm backoff delay giữa retries:
  ```python
  delay = min(2 ** error_retry_count, 30)  # cap 30s
  time.sleep(delay)
  ```
- [ ] **P3.2** — Phân loại error types:
  - `VSCodeChatRetryableError` → retry with backoff
  - `VSCodeChatError` (non-retryable) → stop ngay
  - `json.JSONDecodeError` / parse errors → retry (LLM quirk)
  - `FileNotFoundError` / `PermissionError` → stop ngay (config/env issue)
- [ ] **P3.3** — Sau 2 consecutive errors, thêm `--new-session` hint vào prompt:
  ```
  "Previous session had errors. Start fresh analysis of the workspace."
  ```
- [ ] **P3.4** — Ghi `error_type` và `retryable` flag vào state.json errors array
- [ ] **P3.5** — Config option: `maxErrorRetries` (default 3), `errorBackoffBaseSeconds` (default 2)

### Test

- [ ] **T3.1** — `test_backoff_delay_between_retries`:
  Mock time.sleep, trigger 2 errors → assert sleep called with 2, 4
- [ ] **T3.2** — `test_fatal_error_stops_immediately`:
  Raise FileNotFoundError → assert run stops after 1 error, not 3
- [ ] **T3.3** — `test_retryable_error_continues`:
  Raise VSCodeChatRetryableError 2 lần → assert continues, lần 3 → stops
- [ ] **T3.4** — `test_error_type_logged_in_state`:
  Trigger error → assert state.json errors[] has error_type + retryable fields
- [ ] **T3.5** — `test_session_reset_hint_after_consecutive_errors`:
  Trigger 2 errors → assert next prompt contains "Start fresh"

### Tiêu chí hoàn thành
- Retry chờ 2s, 4s, 8s (cap 30s)
- Fatal error dừng ngay, không retry
- Retryable error retry đúng 3 lần
- State.json ghi rõ loại error
- Lint + test pass

---

## Phase 4 — Risk Gate & Escalation

### Vấn đề
Protected path violation → ngay lập tức error + rollback. Không cho human cơ hội approve.
Không có khái niệm "đợi human quyết định rồi tiếp".

### Giải pháp
1. Thêm decision action `escalate` bên cạnh `continue`/`done`/`blocked`
2. Protected path violation → escalate thay vì error
3. Ghi `pendingEscalation` vào state.json với context đầy đủ
4. Thêm CLI command `approve` / `reject` để human quyết định
5. Khi approve → resume từ iteration đó, giữ changes
6. Khi reject → rollback + resume hoặc stop

### Checklist

- [ ] **P4.1** — Thêm `action='escalate'` vào Decision dataclass
- [ ] **P4.2** — Trong `operator.py`, thay vì error khi protected path violation:
  ```python
  if protected_violations:
      self.runtime['pendingEscalation'] = {
          'type': 'protected_path',
          'paths': protected_violations,
          'iteration': iteration,
          'diff_summary': get_diff_summary(workspace),
          'timestamp': self._now(),
      }
      self.runtime['status'] = 'escalated'
      # ... save state, return with reasonCode='ESCALATION_REQUIRED'
  ```
- [ ] **P4.3** — Thêm CLI `approve-escalation`:
  - Đọc pendingEscalation từ state
  - In context (paths, diff summary)
  - Xóa pendingEscalation, set pendingDecision.action='continue'
  - Auto-trigger resume
- [ ] **P4.4** — Thêm CLI `reject-escalation`:
  - Rollback changes
  - Set pendingDecision với adjusted prompt: "Do NOT modify these paths: ..."
  - Auto-trigger resume
- [ ] **P4.5** — Thêm `escalation_policy` vào config:
  ```yaml
  escalationPolicy:
    protectedPaths: escalate  # 'escalate' | 'block' | 'warn'
    largeDiff: warn           # diff > N lines → warn
    largeDiffThreshold: 200
  ```
- [ ] **P4.6** — Detect large diffs (> threshold) → log warning, optional escalation

### Test

- [ ] **T4.1** — `test_protected_path_triggers_escalation_not_error`:
  Config escalationPolicy=escalate → modify protected path → assert status='escalated'
- [ ] **T4.2** — `test_approve_escalation_resumes_run`:
  Create escalated state → approve → assert run continues
- [ ] **T4.3** — `test_reject_escalation_rollbacks_and_adjusts_prompt`:
  Create escalated state → reject → assert rollback + prompt has "Do NOT modify"
- [ ] **T4.4** — `test_block_policy_maintains_old_behavior`:
  Config escalationPolicy=block → assert error behavior unchanged
- [ ] **T4.5** — `test_large_diff_warning`:
  Generate diff > threshold → assert warning logged
- [ ] **T4.6** — `test_escalation_state_has_full_context`:
  Assert pendingEscalation has paths, diff_summary, iteration, timestamp

### Tiêu chí hoàn thành
- Protected path → escalate (mặc định) thay vì error
- `approve-escalation` → tiếp tục giữ changes
- `reject-escalation` → rollback + adjust prompt
- Config cho phép chọn policy: escalate/block/warn
- Lint + test pass

---

## Phase 5 — Session Context Optimization

### Vấn đề
Mỗi iteration tạo chat session mới → mất toàn bộ context trước đó.
Copilot phải đọc lại files, hiểu lại codebase từ đầu → chậm + lãng phí.

### Giải pháp
**Không** cố reuse session (VS Code CLI không hỗ trợ `--session-id`).
Thay vào đó, tối ưu context injection để session mới vẫn hiệu quả:

1. **Compact history**: Thêm diff summary của iteration trước vào prompt
2. **Progressive context**: Iteration 1 có repo map đầy đủ, iteration 2+ chỉ có focused files
3. **Changed files tracking**: Ghi lại files đã thay đổi → inject vào prompt sau
4. **Memory file enrichment**: Sau mỗi iteration, ghi key decisions vào memory.md

### Checklist

- [ ] **P5.1** — Sau mỗi iteration, ghi vào `memory.md`:
  - Files đã thay đổi (từ git diff)
  - Validation results tóm tắt
  - Key decisions và lý do
- [ ] **P5.2** — Trong `build_follow_up_prompt()`:
  - Thêm section "Files changed in previous iterations"
  - Thêm diff summary compact (max 2000 chars)
- [ ] **P5.3** — Repo map scoring theo goal + changed files:
  - Files đã chạm → score cao hơn → xuất hiện đầu repo map
- [ ] **P5.4** — `--add-file` cho changed files:
  - Iteration 2+: attach files đã sửa ở iteration trước bằng `--add-file`
  - Copilot không cần tự đọc lại
- [ ] **P5.5** — Compact prompt khi quá dài:
  - Nếu total context > 10000 chars → truncate repo map, giữ history + changed files

### Test

- [ ] **T5.1** — `test_memory_file_updated_after_iteration`:
  Run 1 iteration → assert memory.md has changed files section
- [ ] **T5.2** — `test_follow_up_prompt_has_changed_files`:
  Simulate iteration 2 → assert prompt contains "Files changed"
- [ ] **T5.3** — `test_repo_map_prioritizes_changed_files`:
  Mark files as changed → build repo map → assert changed files appear first
- [ ] **T5.4** — `test_add_file_includes_changed_files`:
  Simulate iteration 2 → assert --add-file args include previously changed files
- [ ] **T5.5** — `test_prompt_truncation_preserves_history`:
  Create very long context → assert history section preserved, repo map truncated

### Tiêu chí hoàn thành
- Memory.md tự cập nhật sau mỗi iteration
- Iteration 2+ biết files nào đã thay đổi
- Changed files attach bằng --add-file
- Prompt không quá dài, ưu tiên history > repo map
- Lint + test pass

---

## Phase 6 — E2E Stress Test

### Vấn đề
Chỉ test goal đơn giản "thêm function power()". Chưa test:
- Goal phức tạp multi-file
- Goal có test fail cần fix
- Goal refactor lớn
- Edge cases: no git, corrupted state, VS Code not running

### Giải pháp
Tạo bộ test scenarios đa dạng, chạy E2E trên test repo.

### Checklist

- [ ] **P6.1** — Test scenario: Bug fix
  - Tạo test repo có failing test → goal: "fix the failing test" → assert test pass
- [ ] **P6.2** — Test scenario: Multi-file feature
  - Goal: "thêm module authentication với login/logout/register vào src/ và tests/"
  - Assert: 2+ files created, tests pass
- [ ] **P6.3** — Test scenario: Refactor
  - Tạo code có code smell → goal: "refactor calculator.py to use a Calculator class"
  - Assert: class created, existing tests still pass
- [ ] **P6.4** — Test scenario: Loop recovery
  - Goal cố ý gây loop → assert: loop detected, strategy applied, eventually stops
- [ ] **P6.5** — Test scenario: Validation failure recovery
  - Tạo test repo có lint rule strict → goal: thêm code → lint fail → assert: auto-fix lint
- [ ] **P6.6** — Test scenario: Resume after interrupt
  - Start run → kill midway → resume → assert: continues correctly
- [ ] **P6.7** — Edge case: No git repo
  - Run trong folder không có .git → assert: graceful warning, not crash
- [ ] **P6.8** — Edge case: Corrupted state.json
  - Corrupt state file → run → assert: graceful recovery or clean start
- [ ] **P6.9** — Benchmark suite update
  - Thêm scenarios trên vào `benchmark.json` cho regression testing

### Test

Tất cả P6.x đều là test. Tiêu chí:
- [ ] **T6.1** — Mỗi scenario chạy thành công ít nhất 2/3 lần (account cho LLM variance)
- [ ] **T6.2** — Không scenario nào crash (exit code khác 0 do exception)
- [ ] **T6.3** — Tổng thời gian chạy < 10 phút cho full suite

### Tiêu chí hoàn thành
- 8+ scenarios documented + tested
- Benchmark suite cập nhật
- No crashes trên edge cases

---

## Phase 7 — Level 2 Graduation

### Vấn đề
Cần audit tổng thể + tag stable release.

### Checklist

- [ ] **P7.1** — Chạy full test suite: `python -m unittest discover`
- [ ] **P7.2** — Chạy full lint: `ruff check`
- [ ] **P7.3** — Chạy benchmark suite (Phase 6)
- [ ] **P7.4** — Code review: kiểm tra TODO/FIXME/HACK comments
- [ ] **P7.5** — Update README.md:
  - Cập nhật trạng thái Level 2
  - Thêm "Quick start" section
  - Thêm "How it works" diagram
- [ ] **P7.6** — Update HUONG_DAN_SU_DUNG.md nếu có thay đổi CLI
- [ ] **P7.7** — Version bump → `v2.0.0` (Level 2 stable)
- [ ] **P7.8** — Git tag + push
- [ ] **P7.9** — Self-assessment scorecard:

| Tiêu chí | Target | Actual |
|----------|--------|--------|
| Validation auto-fill | ✅ | ? |
| Auto-resume | ✅ | ? |
| Error recovery (backoff + type) | ✅ | ? |
| Risk escalation | ✅ | ? |
| Context optimization | ✅ | ? |
| E2E stress pass rate | ≥ 80% | ? |
| Test count | ≥ 300 | ? |
| Lint clean | ✅ | ? |

### Tiêu chí hoàn thành
- Scorecard ≥ 7/8 đạt
- v2.0.0 tagged + pushed
- Toàn bộ test + lint pass

---

## Thứ tự thực thi khuyến nghị

```
Phase 1 (Validation Auto-Fill)     ← nhỏ, high impact, làm trước
    ↓
Phase 2 (Auto-Resume)              ← nhỏ, UX killer feature
    ↓
Phase 3 (Error Recovery)           ← trung bình, reliability
    ↓
Phase 4 (Risk Gate)                ← trung bình, safety
    ↓
Phase 5 (Context Optimization)     ← trung bình, performance
    ↓
Phase 6 (E2E Stress Test)          ← validation cho tất cả trên
    ↓
Phase 7 (Graduation)               ← tag v2.0.0
```

Mỗi phase commit riêng, tag minor version (v1.3.0, v1.4.0, ...).
Phase 7 tag v2.0.0 khi tất cả pass.

---

## Những thứ KHÔNG làm trong Level 2

- ❌ Multi-worker / scheduler integration (Level 3)
- ❌ Dashboard / web UI (Level 3)
- ❌ Click chuột / UI automation
- ❌ Patch VS Code / Copilot extension
- ❌ Cross-repo brain nâng cao
- ❌ GitHub issue auto-pick (có module nhưng chưa cần kích hoạt)
- ❌ Prompt optimization sâu (cái ăn tiền là state management, không phải prompt)
