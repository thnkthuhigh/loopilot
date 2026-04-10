# Copilot Operator — Tổng Quan Dự Án

> **Phiên bản:** 2.3.0  
> **Giấy phép:** MIT  
> **Python:** ≥ 3.10  
> **Trạng thái:** Battle-tested on real repos, platform-fragile  
> **Cập nhật:** 2026-04-10  
> **Local commits:** 5 ahead of origin/main

---

## Mục lục

1. [Dự án là gì?](#1-dự-án-là-gì)
2. [Kiến trúc tổng quan](#2-kiến-trúc-tổng-quan)
3. [Danh sách module (33 file, ~10.399 dòng)](#3-danh-sách-module)
4. [CLI — 23 lệnh](#4-cli--23-lệnh)
5. [Tính năng đã hoàn thành](#5-tính-năng-đã-hoàn-thành)
6. [Hệ thống test (421 test, 17 file)](#6-hệ-thống-test)
7. [Cấu hình (copilot-operator.yml)](#7-cấu-hình)
8. [LLM Brain — 5 provider](#8-llm-brain--5-provider)
9. [Tài liệu hiện có](#9-tài-liệu-hiện-có)
10. [Những gì chưa làm (~15 mục)](#10-những-gì-chưa-làm)
11. [Lộ trình phát triển (Phase 5–8)](#11-lộ-trình-phát-triển)
12. [Kết quả E2E đã chạy](#12-kết-quả-e2e-đã-chạy)
13. [Bug đã fix qua E2E thực chiến](#13-bug-đã-fix-qua-e2e-thực-chiến)
14. [Nhận định tình trạng hiện tại](#14-nhận-định-tình-trạng-hiện-tại)
15. [Chiến lược phát triển tiếp](#15-chiến-lược-phát-triển-tiếp)

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

**Tổng: 33 module, ~10.399 dòng code production**

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

**Tổng: 421 test, 17 file, tất cả PASS** ✅ (lint clean)

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

### Tổng quan: ~15 mục — phần lớn là **tinh chỉnh vận hành** và **mở rộng**

Code production đã hoàn chỉnh. Đã test trên 2 repo thật (note-cli + english). Các mục còn lại là tối ưu hóa và mở rộng.

### ✅ Đã hoàn thành (trước đây nằm trong "chưa làm")

| Mục | Trạng thái |
|-----|-----------|
| Gắn vào repo thật (init, workspace, yml) | ✅ Đã làm trên note-cli + english |
| Tạo repo-profile.yml cho repo thật | ✅ Đã auto-generate |
| Xác nhận VS Code mở đúng repo + workspaceStorage resolve | ✅ Đã fix + test |
| Khai báo gate thật (tests, lint) | ✅ Vitest, Jest, ESLint configured |
| Đánh dấu required, runBeforePrompt | ✅ Đã làm |
| Chạy 1 task bug fix nhỏ | ✅ note-cli task 1 (score 100) |
| Chạy 1 task thêm feature | ✅ note-cli task 2 (NoteCollection, score 95) |
| Chạy 1 task sửa lint/type error | ✅ note-cli task 3 (CLI refactor) |
| Chạy 1 task multi-iteration phức tạp | ✅ note-cli SQLite migration (3 iterations) |
| Chạy trên dự án lớn (production) | ✅ english project — 835 server tests, 61 client tests |
| Tổng kết operator gặp chỗ nào nhiều nhất | ✅ 7 bug đã tìm và fix |

### Còn lại

| Mục | Mô tả |
|-----|-------|
| CI local/remote integration | Trigger GitHub Actions → đọc kết quả → fix loop |
| Stop gate fine-tuning | Tinh chỉnh score target, reason code cho từng domain |
| Benchmark so sánh với đối thủ | Cần baseline từ SWE-Bench / Aider |
| Multi-session thực chiến | Scheduler chạy 2+ sessions song song trên repo thật |
| Runbook bổ sung | Thêm troubleshooting cho các lỗi mới phát hiện |

### Definition of Done — Phase hiện tại

| Mục | Trạng thái |
|-----|-----------|
| Operator chạy được trên repo thật | ✅ 2 repo (note-cli + english) |
| Validation thật đã được điền vào config | ✅ Jest + Vitest + ESLint |
| Có goal templates thật | ✅ 3 templates per project |
| Có 3+ session thành công | ✅ 8 sessions (4 toy + 4 real) |
| Có runbook để người khác đọc và dùng | ⚠️ Có draft, cần bổ sung lỗi mới |

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

#### CI Integration (thuộc Phase 5 gốc — Repo Ops) — Ưu tiên 5
> **Mục tiêu:** Chạy CI local/remote và đọc kết quả
- Trigger CI pipeline (GitHub Actions, GitLab CI)
- Đọc CI result → nếu fail → mở vòng fix Copilot tiếp
- **Trạng thái:** Code integration sẵn sàng nhưng chưa có pipeline thật để test.

#### Phase 5 — UI Driver Fallback — ĐÃ HOÃN
> Ưu tiên thấp. CLI đã xử lý ~95% use case. Dễ nợ kỹ thuật. Chưa đúng thời điểm.

#### Mở rộng tương lai — ĐÃ HOÃN
> Tất cả các mục dưới đây đã hoãn — chưa đúng điểm nghẽn hiện tại.
- Web UI Dashboard, Team collaboration, Plugin system, Cloud deployment, VS Code Extension

---

## 12. Kết quả E2E đã chạy

### Giai đoạn 1: Toy repos (4 runs)

| # | Repo | Goal | Score | Iter | Tests |
|---|------|------|------:|:----:|------:|
| 1 | loopilot-test-repo | `power(base, exp)` | 95 | 1 | 121 |
| 2 | loopilot-test-repo | `power(base, exp)` (lần 2) | 95 | 1 | 121 |
| 3 | loopilot-test-repo | `factorial(n)` | 95 | 1 | 121 |
| 4 | bookstore-api | `Add ShoppingCart class` | 95 | 1 | 31 |

### Giai đoạn 2: note-cli — Python CLI thật (4 runs)

| # | Goal | Score | Iter | Tests sau |
|---|------|------:|:----:|----------:|
| 5 | Bug fix (models + stats) | 100 | 1 | 31 |
| 6 | NoteCollection feature | 95 | 1 | 50 |
| 7 | CLI refactor + testability | 95 | 3 | 60 |
| 8 | SQLite storage migration | 95 | 3 | 78 |

### Giai đoạn 3: english — Full-stack production app (React + Express + MongoDB)

| # | Goal | Kết quả | Iter | Tests sau |
|---|------|---------|:----:|----------:|
| 9 | Create testHelpers.js + MongoMemoryServer | ✅ All pass | 1 | 835 server + 61 client |
| 10 | Fix dependencies.test.js (username validation) | ✅ All pass | 1* | 835 server (28 suites) |

\* Task 10: Copilot hoàn thành code qua VS Code Chat trực tiếp (operator gặp session format mismatch, nhưng code changes đã được commit).

### Kết quả ROI tổng hợp
- **Tổng session:** 10 (4 toy + 4 note-cli + 2 english)
- **Success rate:** 100% (code changes luôn đúng)
- **Multi-iteration tasks:** 2 (SQLite: 3 iter, CLI refactor: 3 iter)
- **Lớn nhất:** english project — 835 tests, full-stack, production code
- **Bug phát hiện & fix:** 7 operator bugs qua E2E thực chiến

---

## 13. Bug đã fix qua E2E thực chiến

| # | Bug | File | Fix |
|---|-----|------|-----|
| 1 | Chat session timeout = fatal error (không retry) | `vscode_chat.py` | Đổi thành `VSCodeChatRetryableError` |
| 2 | `chatSessions/` dir sai (VS Code di chuyển sang `transcripts/`) | `operator.py` | Thêm `_resolve_chat_dir()` thử 3 paths |
| 3 | `code chat --reuse-window` gửi vào window sai | `vscode_chat.py` | `focus_workspace()` trước khi gửi chat |
| 4 | Session summary không ghi khi fatal error | `operator.py` | Thêm `dump_json(summary)` ở tất cả error paths |
| 5 | Windows cp1252 encoding crash khi output có tiếng Việt | `validation.py` | Bỏ `text=True`, decode bytes UTF-8 với `errors='replace'` |
| 6 | `KeyError: 'kind'` — event format thiếu key | `session_store.py` | Skip events không có `kind` |
| 7 | Copilot Chat 0.42+ đổi format transcript hoàn toàn | `session_store.py` | Thêm `_load_event_log_session()` cho format `type/data` mới |

### Config mới thêm qua E2E

| Feature | File | Mô tả |
|---------|------|-------|
| `chatModel` field | `config.py` | Cho phép chọn model trong `copilot-operator.yml` |
| Default model Sonnet | `bootstrap.py` | Chuyển từ Opus (đắt) sang Sonnet 4.5 |
| Git-diff fallback | `operator.py` | Khi session timeout, check git diff → continue nếu có changes |

---

## 14. Nhận định tình trạng hiện tại

### Mô tả thẳng thắn

> A battle-tested Copilot supervisor that has proven itself on real repositories, with strong validation and recovery loops, but still dependent on evolving VS Code/Copilot session surfaces.

### Điểm yếu số 1: Platform coupling

7/7 bug thực chiến đều liên quan đến **session surface** của VS Code/Copilot:

- transcript path thay đổi (`chatSessions/` → `transcripts/`)
- transcript format thay đổi (`kind/k/v` → `type/data`)
- session handling (timeout, completion detection)
- window routing (`--reuse-window` gửi sai window)
- encoding (cp1252 Windows vs UTF-8)

**Nhận xét:** Logic của operator mạnh — validation loop, scoring, recovery đều hoạt động đúng. Nhưng lớp platform coupling dễ bị kéo ngã mỗi khi VS Code hoặc Copilot Chat cập nhật version.

### "Battle-tested" nhưng chưa "generalized-ready"

Đã chạy tốt trên:
- repo nhỏ (toy Python)
- repo vừa (note-cli, Python CLI)
- 1 repo production lớn (english, React + Express + MongoDB)

**Nhưng:** Chưa đủ căn cứ để claim "bất kỳ repo nào có test/lint setup là dùng được ngay". Cần thêm repo với ecosystem khác (Go, Rust, Java, monorepo...) để validate claim đó.

### Multi-session: code có, bằng chứng chưa có

Scheduler, worker, conflict tracker, baton merge — tất cả đã code xong.
Nhưng **chưa từng chạy 2+ sessions song song trên repo thật**.
"Copilot farm" hiện tại là **hứa hẹn kỹ thuật**, chưa phải bằng chứng vận hành.

### Đánh giá thẳng

| Tiêu chí | Điểm | Ghi chú |
|----------|------:|---------|
| Ý tưởng | 9/10 | Autonomous meta-agent điều khiển Copilot — rất khác biệt |
| Độ khác biệt | 8.5/10 | Không tool nào làm đúng cách này |
| Kiến trúc | 8.5/10 | 33 module tách rõ, testable, extensible |
| Thực chiến | 8/10 | 10 sessions, 4 repos, 7 bugs fixed |
| Độ bền dài hạn | 6.5/10 | Platform coupling = rủi ro lớn nhất |
| Tiềm năng (nếu đi đúng) | 9/10 | Rất đáng phát triển tiếp |

### Kết luận

- Không còn là prototype
- Đã có giá trị thật (10 sessions thành công, production repo)
- Rất ấn tượng với tư cách dự án cá nhân
- **Điểm cần làm tiếp là hardening và chiến lược execution, không phải thêm tính năng mới**

---

## 15. Chiến lược phát triển tiếp

### Hướng đi: Internal power tool trước, polish thành public repo sau

**Không productize nặng.** Mục tiêu giai đoạn tiếp:
- Biến nó thành "operating system" cho workflow code cá nhân
- Giảm tối đa công babysit Copilot
- Chạy ổn trên 1–3 repo dùng nhiều nhất
- Song song polish repo công khai (README, docs, case studies)

### Ưu tiên 1: Harden runtime boundary 🔴 CRITICAL

**Vấn đề:** Session/worker lifecycle không rõ ràng. Context bị mất khi CLI tạo session mới. Same-window conflict.

**Cần làm:**
- session isolation rõ hơn (dedicated window per operator run)
- context continuity khi one-shot CLI buộc tạo session mới
- same-window conflict detection (warn/abort nếu window đang bận)
- resume sau lỗi mượt hơn (state checkpoint giữa iteration)

**Lý do #1:** Đây là chỗ sinh lợi nhất — giảm babysit = giá trị cốt lõi.

### Ưu tiên 2: Stop controller mạnh hơn 🟠 HIGH

**Đã có:** score threshold, blocker severity, SLA, expectations.

**Cần thêm:**
- **no-progress detection** — nếu 2 iterations không có diff mới → dừng
- **diff dedup** — phát hiện Copilot lặp code cũ
- **cost ceiling thực chiến** — wall-clock timeout per task (không chỉ per session)
- **hard stop vs soft escalate** — phân biệt "dừng hẳn" vs "báo human"

**Lý do:** Quyết định operator có chạy unattended được hay phải canh.

### Ưu tiên 3: Chuẩn hóa worker contract 🟡 MEDIUM

Nghĩ worker như một **thực thể vận hành**, không chỉ là wrapper:

| Thuộc tính | Mô tả |
|-----------|-------|
| Task input | Nhận task gì, format chuẩn |
| Minimum state | State tối thiểu worker phải giữ |
| Health signal | healthy / degraded / dead |
| Recycle policy | Khi nào kill + restart worker |
| Required artifacts | Output bắt buộc sau mỗi iteration |

**Lý do:** Worker contract sạch → continuous mode sạch → multi-session sạch.

### Ưu tiên 4: Multi-session thực chiến 🟡 MEDIUM

**Không viết thêm code scheduler.** Đem scheduler đã có ra chiến trường:

- 1 worker implement + 1 worker test/audit
- Chạy trên repo thật
- Đo: conflict rate, merge pain, token/time savings
- **Nếu multi-session không thắng rõ ràng → không promote nó thành feature chính**

### Ưu tiên 5: CI integration 🟢 LOW

Bước mở rộng tốt hơn UI driver fallback:
- Trigger GitHub Actions workflow
- Đọc CI result → nếu fail → mở vòng fix tiếp
- Giá trị trực tiếp hơn UI fallback (vốn dễ nợ kỹ thuật)

### Hoãn lại (chưa đúng thời điểm)

| Feature | Lý do hoãn |
|---------|-----------|
| UI Driver Fallback | Dễ nợ kỹ thuật, CLI đã xử lý ~95% case |
| Web UI Dashboard | Chưa đủ user base, TUI đủ dùng |
| Team collaboration | Chưa ổn cho 1 người, chưa nên mở cho nhiều người |
| Plugin system | Over-engineering ở giai đoạn này |
| Cloud deployment | Local-first vẫn đúng strategy |
| VS Code Extension packaging | Cần platform coupling ổn trước |

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

*Tài liệu cập nhật — Copilot Operator v2.3.0 — 2026-04-10 — Honest assessment edition*
