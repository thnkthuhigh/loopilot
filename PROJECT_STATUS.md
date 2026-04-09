# Copilot Operator — Tổng Quan Dự Án

> **Phiên bản:** 2.3.0  
> **Giấy phép:** MIT  
> **Python:** ≥ 3.10  
> **Trạng thái:** Production/Stable  
> **Cập nhật:** 2025-07

---

## Mục lục

1. [Dự án là gì?](#1-dự-án-là-gì)
2. [Kiến trúc tổng quan](#2-kiến-trúc-tổng-quan)
3. [Danh sách module (33 file, ~11.228 dòng)](#3-danh-sách-module)
4. [CLI — 23 lệnh](#4-cli--23-lệnh)
5. [Tính năng đã hoàn thành](#5-tính-năng-đã-hoàn-thành)
6. [Hệ thống test (421 test, 15 file)](#6-hệ-thống-test)
7. [Cấu hình (copilot-operator.yml)](#7-cấu-hình)
8. [LLM Brain — 5 provider](#8-llm-brain--5-provider)
9. [Tài liệu hiện có](#9-tài-liệu-hiện-có)
10. [Những gì chưa làm (32 mục)](#10-những-gì-chưa-làm)
11. [Lộ trình phát triển (Phase 5–8)](#11-lộ-trình-phát-triển)
12. [Kết quả E2E đã chạy](#12-kết-quả-e2e-đã-chạy)

---

## 1. Dự án là gì?

**Copilot Operator** là một **autonomous meta-agent** điều khiển GitHub Copilot Chat trong VS Code để hoàn thành các tác vụ lập trình từ đầu đến cuối — không cần người dùng can thiệp.

Quy trình hoạt động:

```
Goal → Decompose → Prompt Copilot → Validate (test/lint/build) → Score → Loop or Stop
```

- Tự gửi prompt cho Copilot, chờ phản hồi, chạy test/lint/build
- Tự đánh giá kết quả, quyết định tiếp tục hay dừng
- Tự rollback nếu code bị hỏng, retry với chiến lược mới
- Học từ các session trước (meta-learning, memory)
- Hỗ trợ multi-session song song (scheduler)

---

## 2. Kiến trúc tổng quan

```
┌──────────────────────────────────────────────────────────┐
│                    CLI (cli.py)                           │
│   doctor │ init │ run │ resume │ status │ plan │ ...     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Operator    │  │   Planner    │  │  Scheduler     │  │
│  │  (core loop) │  │  (milestones)│  │  (multi-sess.) │  │
│  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                 │                   │          │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌───────▼────────┐ │
│  │  VS Code Chat│  │  Goal        │  │  Worker        │ │
│  │  Driver      │  │  Decomposer  │  │  Runtime       │ │
│  └──────┬───────┘  └──────────────┘  └────────────────┘ │
│         │                                                │
│  ┌──────▼───────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  Bootstrap   │  │  LLM Brain   │  │  Reasoning     │ │
│  │  (settings)  │  │  (5 providers│  │  Engine         │ │
│  └──────────────┘  └──────────────┘  └────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  Validation  │  │  Snapshot    │  │  Benchmark     │ │
│  │  Pipeline    │  │  & Rollback  │  │  & Scoring     │ │
│  └──────────────┘  └──────────────┘  └────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  GitHub      │  │  Dashboard   │  │  Policy        │ │
│  │  Integration │  │  (TUI)       │  │  Engine        │ │
│  └──────────────┘  └──────────────┘  └────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │  Meta-Learner│  │  Brain       │  │  Adversarial   │ │
│  │  (learning)  │  │  (memory)    │  │  Review        │ │
│  └──────────────┘  └──────────────┘  └────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Danh sách module

| # | Module | Dòng | Chức năng |
|---|--------|-----:|-----------|
| 1 | `__init__.py` | 16 | Version, public exports |
| 2 | `__main__.py` | 4 | Entry point (`python -m copilot_operator`) |
| 3 | `adversarial.py` | 215 | Adversarial review — tự phản biện code sinh ra |
| 4 | `benchmark.py` | 355 | Benchmark runner, quality rubric, done guards |
| 5 | `bootstrap.py` | 1.008 | Khởi tạo workspace, auto-approve settings, VS Code config |
| 6 | `brain.py` | 341 | Project brain — long-term memory per repo |
| 7 | `cli.py` | 991 | 23 CLI commands (Click-based) |
| 8 | `config.py` | 265 | `OperatorConfig` dataclass, YAML loader, SLA fields |
| 9 | `cross_repo_brain.py` | 222 | Cross-repo knowledge transfer |
| 10 | `dashboard.py` | 306 | TUI dashboard: iteration status, cost breakdown, repo health |
| 11 | `github_integration.py` | 456 | GitHub API: issues, PRs, labels, blocked alerts |
| 12 | `goal_decomposer.py` | 398 | Phân tách goal lớn thành sub-goals |
| 13 | `intention_guard.py` | 302 | Ngăn Copilot đi lệch khỏi goal gốc |
| 14 | `llm_brain.py` | 735 | LLM integration: 5 providers (OpenAI, Anthropic, Gemini, xAI, local) |
| 15 | `logging_config.py` | 61 | Cấu hình logging chuẩn |
| 16 | `meta_learner.py` | 310 | Học từ session trước: pattern extraction, strategy selection |
| 17 | `nightly.py` | 173 | Nightly delivery loop — chạy tự động hàng đêm |
| 18 | `operator.py` | 1.400 | **Core engine** — vòng lặp chính, history compaction, circuit breaker |
| 19 | `planner.py` | 638 | Milestone planning, acceptance evaluation, gate-based picking |
| 20 | `policy.py` | 217 | Policy engine — rules & approval lanes |
| 21 | `prompts.py` | 453 | Prompt templates cho các loại task |
| 22 | `reasoning.py` | 412 | Reasoning engine — phân tích & quyết định chiến lược |
| 23 | `repo_inspector.py` | 190 | Auto-detect repo type, package manager, frameworks |
| 24 | `repo_map.py` | 368 | Bản đồ cấu trúc repo cho context |
| 25 | `repo_ops.py` | 429 | Git operations: branch, commit, PR, changelog |
| 26 | `roi.py` | 176 | ROI analytics — đo hiệu quả operator |
| 27 | `scheduler.py` | 436 | Multi-session scheduler: roles, conflict tracker, baton merge |
| 28 | `session_store.py` | 160 | Session state persistence (JSONL incremental) |
| 29 | `snapshot.py` | 238 | Git snapshot & rollback |
| 30 | `terminal.py` | 138 | Terminal command runner với timeout |
| 31 | `validation.py` | 88 | Validation pipeline (test/lint/build/smoke) |
| 32 | `vscode_chat.py` | 180 | VS Code CLI driver cho Copilot Chat |
| 33 | `worker.py` | 342 | Worker runtime layer — isolated execution |

**Tổng: 33 module, ~11.228 dòng code production**

---

## 4. CLI — 23 lệnh

### Lệnh cơ bản

| Lệnh | Mô tả |
|-------|--------|
| `copilot-operator doctor` | Kiểm tra môi trường (Python, VS Code, Copilot, `code` CLI) |
| `copilot-operator init` | Tạo `copilot-operator.yml` scaffold cho repo |
| `copilot-operator run --goal "..."` | Chạy vòng lặp operator với goal cho trước |
| `copilot-operator resume` | Tiếp tục session bị gián đoạn |
| `copilot-operator status` | Xem trạng thái session hiện tại |
| `copilot-operator focus` | Mở workspace trong VS Code |
| `copilot-operator version` | Hiển thị version |
| `copilot-operator cleanup` | Dọn dẹp file tạm, session cũ |

### Lệnh nâng cao

| Lệnh | Mô tả |
|-------|--------|
| `copilot-operator plan` | Xem milestone plan cho goal hiện tại |
| `copilot-operator watch` | Theo dõi session realtime |
| `copilot-operator brain` | Xem/quản lý project brain memory |
| `copilot-operator benchmark` | Chạy benchmark suite |
| `copilot-operator dashboard` | Mở TUI dashboard (cost, iterations, health) |

### Lệnh GitHub Integration

| Lệnh | Mô tả |
|-------|--------|
| `copilot-operator issues` | Liệt kê GitHub issues |
| `copilot-operator fix-issue` | Tự động fix một GitHub issue |
| `copilot-operator queue` | Xem task queue |
| `copilot-operator approve-escalation` | Duyệt escalation request |
| `copilot-operator reject-escalation` | Từ chối escalation request |

### Lệnh orchestration

| Lệnh | Mô tả |
|-------|--------|
| `copilot-operator multi` | Chạy multi-session song song |
| `copilot-operator nightly` | Chạy nightly delivery loop |
| `copilot-operator roi` | Xem ROI analytics |
| `copilot-operator policy` | Quản lý policy rules |

---

## 5. Tính năng đã hoàn thành

### Phase 1 — Production Operator ✅

- [x] Vòng lặp tự trị (operator loop) với circuit breaker
- [x] Validation pipeline: test, lint, build, smoke — cấu hình qua YAML
- [x] Resume session từ state.json
- [x] Decision log có cấu trúc (JSON per iteration)
- [x] Error recovery: phát hiện lỗi → rollback → retry với chiến lược mới
- [x] Stop gate: score threshold + blocker severity
- [x] History compaction: giữ 5 entry gần nhất đầy đủ, còn lại chỉ giữ key data
- [x] Prompt contract: structured prompt templates cho 6 loại task
- [x] Auto-approve VS Code settings (autoSave, hotExit, chat.editing.confirmations)
- [x] Session cleanup (xóa session quá hạn)

### Phase 2 — Project Brain ✅

- [x] `repo-profile.yml` — profile cho từng repo
- [x] `definition-of-done.md` — tiêu chí hoàn thành
- [x] `architecture-map.md` — bản đồ kiến trúc
- [x] `validation-map.md` — ánh xạ validation commands
- [x] `known-traps.md` — bẫy đã gặp & cách xử lý
- [x] Cross-repo brain — chia sẻ kiến thức giữa các repo
- [x] Meta-learner — học pattern từ session trước

### Phase 3 — Autonomous Planner ✅

- [x] Goal decomposition: tách goal lớn thành sub-goals
- [x] Milestone-based planning với task queue
- [x] Acceptance evaluation: đánh giá milestone dựa trên criteria
- [x] Gate-based milestone picking: tự nhảy qua milestone đã đạt
- [x] Intention guard: ngăn Copilot đi lệch goal
- [x] Adversarial review: tự phản biện kết quả
- [x] Reasoning engine: phân tích chiến lược

### Phase 4 — Multi-Session ✅

- [x] Scheduler với session roles (implement/test/audit/docs)
- [x] File conflict tracker (file locking giữa sessions)
- [x] Baton merge (gộp output từ nhiều sessions)
- [x] Shared stop gate (cross-session acceptance)
- [x] Scheduler persistence (JSON save/load)
- [x] Worker runtime layer

### Phase 5 — Repo Ops (Partial)

- [x] Branch creation & naming strategy
- [x] Checkpoint commits
- [x] Commit message formatter (conventional commits)
- [x] PR summary generation
- [x] Changelog generation (nhóm theo feat/fix/docs/refactor/test/chore)
- [ ] CI local/remote integration (chưa làm)

### Phase 6 — Delivery System ✅

- [x] GitHub issue tracker integration (list, create, update, close)
- [x] Nightly delivery loop
- [x] Blocked alerts (post comment + update labels)
- [x] Goal queue by priority

### Phase 7 — Dashboard & Analytics ✅

- [x] TUI dashboard (iteration status, pass/fail, score)
- [x] Cost breakdown per iteration (prompt/completion tokens)
- [x] Blocker trend visualization
- [x] Repo health metrics
- [x] ROI analytics (success rate, time saved, cost per task)
- [x] Policy engine (approval rules, cost ceilings)

### Phase 8 — Quality & Benchmarking ✅

- [x] Benchmark runner (run suite, compare scores)
- [x] Quality rubric (per-profile rules: bug/feature/refactor/audit/docs)
- [x] Done guards (evaluate completion criteria)
- [x] SLA config (max blocked time, max cost per hour)
- [x] Output expectations (expect_tests_added, expect_docs_updated, expect_max_files_changed)

### LLM Brain ✅

- [x] OpenAI (GPT-4, GPT-4o, GPT-3.5)
- [x] Anthropic (Claude 3 Opus, Sonnet, Haiku)
- [x] Google Gemini (Gemini Pro, Gemini Flash)
- [x] xAI / Grok (Grok-2, Grok-beta)
- [x] Local / Ollama (bất kỳ model local nào)
- [x] Auto-fallback giữa các provider
- [x] Cost tracking per provider

---

## 6. Hệ thống test

**Tổng: 421 test, 15 file, tất cả PASS**

| File test | Số test | Nội dung |
|-----------|--------:|----------|
| `tests/hooks.test.cjs` | 2 | Node.js hooks (state-gate, pre-chat) |
| `tests_python/test_operator.py` | 58 | Core operator loop, circuit breaker, retry logic |
| `tests_python/test_new_modules.py` | 48 | Brain, meta-learner, adversarial, snapshot, reasoning |
| `tests_python/test_new_features.py` | 54 | Goal decomposer, planner, scheduler, repo ops, prompts |
| `tests_python/test_features_af.py` | 70 | LLM brain, repo map, intention guard, session store, cross-repo brain |
| `tests_python/test_hardening.py` | 50 | Dashboard, nightly, ROI, policy, GitHub integration, worker |
| `tests_python/test_llm_brain.py` | 47 | LLM provider tests (all 5 providers) |
| `tests_python/test_terminal_benchmark.py` | 51 | Terminal runner, benchmark suite |
| `tests_python/test_checklist_features.py` | 41 | v2.3.0 features: SLA, compaction, changelog, conflicts, rubric |

### Chạy test

```bash
# Chạy toàn bộ
python -m unittest discover -s tests_python -p "test_*.py"

# Chạy 1 file
python -m unittest tests_python/test_operator.py

# Chạy với npm (bao gồm cả Node.js tests)
npm test

# Lint
python -m ruff check copilot_operator/ tests_python/
```

---

## 7. Cấu hình

File `copilot-operator.yml` — tạo bằng `copilot-operator init`:

```yaml
workspace: "."                          # Đường dẫn workspace
mode: agent                             # ask | edit | agent | custom
goalProfile: default                    # default | bug | feature | refactor | audit | docs
maxIterations: 6                        # Số vòng lặp tối đa
pollIntervalSeconds: 2                  # Khoảng thời gian poll status
sessionTimeoutSeconds: 900              # Timeout session (15 phút)
targetScore: 85                         # Điểm threshold để dừng
failOnBlockers: [critical, high]        # Severity nào thì dừng

# SLA
sla_max_blocked_seconds: 600            # Tối đa bị block bao lâu
sla_max_cost_per_hour_usd: 5.0          # Chi phí tối đa/giờ

# Output expectations
expect_tests_added: true                # Yêu cầu thêm test
expect_docs_updated: false              # Yêu cầu cập nhật docs
expect_max_files_changed: 20            # Tối đa số file thay đổi

# Validation commands
validation:
  - name: tests
    command: "python -m pytest"
    required: true
    timeoutSeconds: 120
  - name: lint
    command: "python -m ruff check ."
    required: true
    timeoutSeconds: 30
  - name: build
    command: "python -m py_compile main.py"
    required: false
    timeoutSeconds: 30

# LLM Brain (optional)
llm:
  provider: xai                         # openai | anthropic | gemini | xai | local
  model: grok-3-mini                    # Model name
  maxTokens: 4096
  temperature: 0.3
```

---

## 8. LLM Brain — 5 provider

| Provider | Models | API Key Env |
|----------|--------|-------------|
| OpenAI | GPT-4, GPT-4o, GPT-3.5 Turbo | `OPENAI_API_KEY` |
| Anthropic | Claude 3 Opus, Sonnet, Haiku | `ANTHROPIC_API_KEY` |
| Google Gemini | Gemini Pro, Gemini Flash | `GEMINI_API_KEY` |
| xAI / Grok | Grok-2, Grok-beta, Grok-3-mini | `XAI_API_KEY` |
| Local (Ollama) | Bất kỳ model local | `OLLAMA_BASE_URL` |

LLM Brain được dùng cho:
- Reasoning (phân tích lỗi, chọn chiến lược)
- Adversarial review (phản biện code)
- Goal decomposition (tách goal phức tạp)
- Meta-learning (rút pattern từ history)

---

## 9. Tài liệu hiện có

| File | Nội dung |
|------|----------|
| `README.md` | Tổng quan dự án, quick start, feature list |
| `HUONG_DAN_SU_DUNG.md` | Hướng dẫn sử dụng tiếng Việt (3 bước) |
| `LICENSE` | MIT License |
| `docs/COPILOT_OPERATOR_MASTER_PLAN.md` | Kế hoạch 8 phase chi tiết |
| `docs/COPILOT_OPERATOR_CHECKLIST.md` | Checklist triển khai (71/103 ✓) |
| `docs/COPILOT_OPERATOR_BACKLOG.md` | Backlog items (OP-001 → OP-016) |
| `docs/COPILOT_OPERATOR_GOAL_TEMPLATES.md` | 6 goal templates mẫu |
| `docs/COPILOT_OPERATOR_RUNBOOK.md` | Runbook vận hành |
| `docs/COPILOT_OPERATOR_QUICK_CHECKLIST.md` | Checklist nhanh |
| `docs/operator/repo-profile.md` | Hướng dẫn repo profile |
| `docs/operator/definition-of-done.md` | Tiêu chí definition of done |
| `docs/operator/architecture-map.md` | Bản đồ kiến trúc |
| `docs/operator/validation-map.md` | Ánh xạ validation |
| `docs/operator/known-traps.md` | Bẫy đã biết & cách xử lý |

---

## 10. Những gì chưa làm

### Tổng quan: 32 mục chưa hoàn thành — tất cả là **tác vụ vận hành** (không phải code)

Toàn bộ code đã viết xong. Các mục còn lại yêu cầu **gắn vào repo thật** và **chạy thực tế**.

### B1. Gắn vào repo thật (6 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Copy/move operator vào repo mục tiêu | Cần chọn repo thật để gắn |
| Chỉnh workspace trong yml | Phụ thuộc vào repo mục tiêu |
| Tạo repo-profile.yml cho repo mục tiêu | Cần phân tích repo thật |
| Chạy `operator:doctor` | Cần repo thật đã setup |
| Xác nhận VS Code mở đúng repo | Cần repo thật |
| Xác nhận workspaceStorage resolve đúng | Cần repo thật |

### B2. Khai báo gate thật (8 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Xem command operator infer có đúng không | Cần chạy trên repo thật |
| Điền command tests nếu infer chưa đúng | Phụ thuộc repo |
| Điền command lint nếu infer chưa đúng | Phụ thuộc repo |
| Điền command build nếu infer chưa đúng | Phụ thuộc repo |
| Thêm smoke nếu cần | Phụ thuộc repo |
| Đánh dấu command nào là required | Phụ thuộc repo |
| Chọn command nào cần runBeforePrompt | Phụ thuộc repo |
| Chạy từng command bằng tay 1 lần | Cần repo thật |

### B3. Goal contract (4 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Tạo goal.txt mẫu riêng cho repo thật | Cần domain knowledge |
| Gán goal profile cho từng loại task thật | Cần task thật |
| Thêm rule theo domain thật của repo | Cần domain thật |
| Định nghĩa rõ khi nào được gọi là done | Cần tiêu chí của repo thật |

### B4. Stop gate thực chiến (6 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Định nghĩa score target cho repo | Cần kinh nghiệm thực tế |
| Định nghĩa blocker severity nào là cấm stop | Cần kinh nghiệm thực tế |
| Định nghĩa khi nào tests có thể là not_applicable | Cần domain knowledge |
| Định nghĩa khi nào lint có thể là not_applicable | Cần domain knowledge |
| Định nghĩa khi nào cho phép blocked | Cần domain knowledge |
| Chốt bảng reason code nào được phép stop thật | Cần kinh nghiệm thực tế |

### B5. Vận hành 3 session mẫu (5 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Chạy 1 task bug fix nhỏ | Cần repo thật có bug |
| Chạy 1 task thêm test | Cần repo thật cần test |
| Chạy 1 task sửa lint/type error | Cần repo thật có lint error |
| Lưu lại memory/state/logs cả 3 task | Phụ thuộc 3 task trên |
| Tổng kết operator gặp chỗ nào nhiều nhất | Phụ thuộc 3 task trên |

### Các mục khác (3 mục)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Khai báo package manager thật cho repo mục tiêu | Phụ thuộc repo |
| Chuyển sang repo thật và điền nội dung thật (memory) | Cần repo thật |
| Định nghĩa danh sách benchmark cần so | Cần xác định đối thủ/baseline |

### Definition of Done — Phase hiện tại (5 mục cuối)

| Mục | Lý do chưa làm |
|-----|-----------------|
| Operator chạy được trên repo thật của bạn | Cần chọn & setup repo thật |
| Validation thật đã được điền vào config | Phụ thuộc repo thật |
| Có 3-5 prompt template thật | Cần domain knowledge |
| Có 3 session thành công liên tiếp | Cần chạy thử thật |
| Có runbook để người khác đọc và dùng | Đã có draft, cần bổ sung |

---

## 11. Lộ trình phát triển

### Đã hoàn thành

| Phase | Tên | Trạng thái |
|-------|-----|------------|
| Phase 1 | Production Operator | ✅ Hoàn thành |
| Phase 2 | Project Brain | ✅ Hoàn thành |
| Phase 3 | Autonomous Planner | ✅ Hoàn thành |
| Phase 4 | Multi-Session Copilot Farm | ✅ Hoàn thành |
| Phase 6 | Delivery System | ✅ Hoàn thành |
| Phase 7 | Dashboard & Analytics | ✅ Hoàn thành |
| Phase 8 | Quality & Benchmarking | ✅ Hoàn thành |

### Trong dự định (chưa code)

#### Phase 5 — UI Driver Fallback
> **Mục tiêu:** Tự động hóa VS Code UI khi CLI không đủ
- UI action layer (click, type, select menu)
- Selector & image registry cho các VS Code elements
- Watchdog phát hiện VS Code bị treo/crash
- **Lý do chưa làm:** Ưu tiên thấp — CLI đã xử lý được ~95% use case. Chỉ cần khi gặp dialog/popup không có CLI equivalent.

#### CI Integration (thuộc Phase 5 gốc — Repo Ops)
> **Mục tiêu:** Chạy CI local/remote và đọc kết quả
- Trigger CI pipeline (GitHub Actions, GitLab CI)
- Đọc CI result → nếu fail → mở vòng fix Copilot tiếp
- **Lý do chưa làm:** Cần GitHub Actions workflow thật trên repo mục tiêu. Code integration sẵn sàng nhưng chưa có pipeline thật để test.

#### Mở rộng tương lai (chưa lên kế hoạch cụ thể)
- **Web UI Dashboard** — thay thế TUI bằng web-based dashboard
- **Team collaboration** — nhiều người dùng chia sẻ operator
- **Plugin system** — cho phép thêm custom validation/strategy
- **Cloud deployment** — chạy operator trên cloud thay vì local
- **VS Code Extension** — đóng gói thành extension thay vì CLI

---

## 12. Kết quả E2E đã chạy

### 4 lần chạy thành công

| # | Repo | Goal | Score | Iterations | Tests |
|---|------|------|------:|:----------:|------:|
| 1 | loopilot-test-repo | `power(base, exp)` | 95 | 1 | 121 |
| 2 | loopilot-test-repo | `power(base, exp)` (lần 2) | 95 | 1 | 121 |
| 3 | loopilot-test-repo | `factorial(n)` | 95 | 1 | 121 |
| 4 | bookstore-api | `Add ShoppingCart class` | 95 | 1 | 31 |

### Kết quả ROI tổng hợp
- **Tổng session:** 4
- **Success rate:** 100%
- **Trung bình iterations:** 1
- **Trung bình score:** 95

---

## Cài đặt nhanh

```bash
# Clone repo
git clone https://github.com/thnkthuhigh/loopilot.git
cd loopilot

# Cài đặt
pip install -e .

# Kiểm tra
copilot-operator doctor

# Khởi tạo config cho repo mục tiêu
cd /path/to/your-repo
copilot-operator init

# Chạy
copilot-operator run --goal "Viết function tính giai thừa"
```

---

*Tài liệu tạo tự động — Copilot Operator v2.3.0*
