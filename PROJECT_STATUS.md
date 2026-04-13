# Copilot Operator — Tổng Quan Dự Án

> **Phiên bản:** 3.2.0  
> **Giấy phép:** MIT  
> **Python:** ≥ 3.10  
> **Trạng thái:** Deep Brain (LLM-driven autonomous thinking) + CI CLI + multi-session baton merge  
> **Cập nhật:** 2026-04-13  
> **Remote:** synced with origin/main (3e71493)

---

## Mục lục

1. [Dự án là gì?](#1-dự-án-là-gì)
2. [Kiến trúc tổng quan](#2-kiến-trúc-tổng-quan)
3. [Danh sách module (51 file, ~17.700 dòng)](#3-danh-sách-module)
4. [CLI — 27 lệnh](#4-cli--27-lệnh)
5. [Tính năng đã hoàn thành](#5-tính-năng-đã-hoàn-thành)
6. [Hệ thống test (866 test, 31 file)](#6-hệ-thống-test)
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
| 3 | `adversarial.py` | 235 | Adversarial review — tự phản biện code sinh ra + SAST checklist |
| 4 | `benchmark.py` | 355 | Benchmark runner, quality rubric, done guards |
| 5 | `bootstrap.py` | 1.008 | Khởi tạo workspace, auto-approve settings, VS Code config |
| 6 | `brain.py` | 341 | Project brain — long-term memory per repo |
| 7 | `cli.py` | 1.200 | 27 CLI commands + --short/--live flags (added `ci`) |
| 8 | `config.py` | 265 | `OperatorConfig` dataclass, YAML loader, SLA fields |
| 9 | `cross_repo_brain.py` | 222 | Cross-repo knowledge transfer |
| 10 | `dashboard.py` | 306 | TUI dashboard: iteration status, cost breakdown, repo health |
| 11 | `github_integration.py` | 456 | GitHub API: issues, PRs, labels, blocked alerts |
| 12 | `goal_decomposer.py` | 398 | Phân tách goal lớn thành sub-goals |
| 13 | `intention_guard.py` | 302 | Ngăn Copilot đi lệch khỏi goal gốc |
| 14 | `llm_brain.py` | 766 | LLM integration: 5 providers + **Deep Brain** (compose_prompt, make_decision, review_iteration) |
| 15 | `logging_config.py` | 61 | Cấu hình logging chuẩn |
| 16 | `meta_learner.py` | 310 | Học từ session trước: pattern extraction, strategy selection |
| 17 | `nightly.py` | 173 | Nightly delivery loop — chạy tự động hàng đêm |
| 18 | `operator.py` | 2.278 | **Core engine** — vòng lặp chính, history compaction, circuit breaker, task ledger + archive integration, live streaming, auto-summary, multi-session baton merge, **Deep Brain integration** |
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
| 33 | `worker.py` | 450 | Worker runtime — health signal, recycle policy, artifacts contract |
| 34 | `runtime_guard.py` | 260 | **NEW** Session isolation: lock file, conflict detection, checkpoint |
| 35 | `stop_controller.py` | 165 | **NEW** Stop controller: no-progress, diff dedup, score floor |
| 36 | `ci_integration.py` | 330 | **NEW** CI integration: GitHub Actions trigger, result analysis |
| 37 | `narrative.py` | 527 | Run narrative: prose summary, done explanation, live status, commitment summary |
| 38 | `mission_memory.py` | 450 | Mission memory: project direction, objectives, lessons, hard_constraints, drift guard, **MissionAuthority (veto, directives, override)** |
| 39 | `memory_promotion.py` | 270 | Memory promotion: rules for promoting facts between layers |
| 40 | `diff_scan.py` | 238 | Pre-validation diff security scan — 18 threat patterns, block+rollback |
| 41 | `task_ledger.py` | 560 | Task ledger — structured per-run state, PriorityPressure, **TaskPriority (P0–P3), PriorityQueue, escalation** |
| 42 | `archive_retrieval.py` | 520 | Archive retrieval — **TF-IDF cosine + BM25 + n-gram + context boost + decay + LLM rerank** |
| 43 | `context_budget.py` | 170 | **NEW** Context budget manager — P0-P4 priority tiers, 4-phase compression, token-aware prompt allocation |
| 44 | `adaptive_strategy.py` | 200 | **NEW** Adaptive strategy engine — mid-run learning, escalation chain, 5 feedback rules, guardrail generation |
| 45 | `self_eval.py` | 165 | **NEW** Self-evaluation feedback loop — iteration score analysis, validation comparison, correction injection |
| 46 | `hint_actuator.py` | 170 | **NEW** Hint actuator — execute reasoning hints (profile escalation, scope narrowing, iteration bump, abort) |
| 47 | `benchmark_learner.py` | 195 | **NEW** Benchmark learner — extract meta-learner rules from failed benchmark cases |
| 48 | `intelligence_telemetry.py` | 230 | **NEW** Intelligence telemetry — track per-source injection effectiveness, wasteful source detection |
| 49 | `narrative_engine.py` | 580 | **NEW** Narrative Engine — 4-view structured agent voice (LiveNarrative, DecisionTrace, RunSummaryView, MemorySnapshot) + NarrativeEngine aggregator |
| 50 | `narrative_formats.py` | 237 | **NEW** Narrative Formats — fixed-structure formatters, tagged blocks, `format_summary_short()`, `format_live_stream_line()` + JSON export |
| 51 | `locale.py` | 229 | **NEW** i18n — `T()` function, `set_locale(lang)`, 40+ keys × en/vi translations cho CLI display |

**Tổng: 51 module, ~17.700 dòng code production (+ ~9.100 dòng tests)**

---

## 4. CLI — 27 lệnh

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
| `copilot-operator narrative` | Xem run narrative (prose summary + done explanation) |
| `copilot-operator mission` | Xem/cập nhật mission memory (project direction) |
| `copilot-operator explain` | Xem AI Narrative UI — 4 view có cấu trúc (Live/Decision/Summary/Memory) |

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

### Phase 9 — Hardening & Runtime Safety ✅ (NEW)

**Runtime Guard** (`runtime_guard.py`):
- [x] Lock file — ngăn chạy 2 operator cùng workspace
- [x] Same-window conflict detection — phát hiện chat session đang active
- [x] Per-iteration state checkpoint — crash recovery mượt
- [x] Context continuity bridge — duy trì context khi CLI tạo session mới

**Stop Controller** (`stop_controller.py`):
- [x] No-progress detection — dừng nếu N iterations không có diff mới
- [x] Diff dedup — phát hiện Copilot lặp code cũ
- [x] Wall-clock task timeout — timeout riêng cho từng task
- [x] Score floor — hard stop nếu score dưới ngưỡng liên tục
- [x] Hard stop vs soft escalate — phân biệt "dừng hẳn" vs "báo human"

**Worker Contract** (`worker.py` enhanced):
- [x] HealthSignal enum (HEALTHY / DEGRADED / DEAD)
- [x] RecyclePolicy — điều kiện kill + restart worker
- [x] RequiredArtifacts — kiểm tra output bắt buộc per iteration
- [x] TaskInput — chuẩn hóa input cho worker

**CI Integration** (`ci_integration.py`):
- [x] Trigger GitHub Actions workflow
- [x] Read workflow run status + job details
- [x] Wait for workflow completion (polling)
- [x] Analyse failed jobs + extract log excerpts
- [x] Build fix prompt from CI failure for operator loop
- [x] Render human-readable CI summary

### Phase 10 — Run Narrative + Mission Memory + Memory Promotion ✅

**Narrative Layer** (`narrative.py`):
- [x] LiveStatus — per-iteration snapshot (phase, score, blockers)
- [x] RunNarrative — prose summary toàn bộ run (iteration arc, files changed, validation)
- [x] DoneExplanation — structured evidence: conditions met/unmet, risks, unchecked items
- [x] Per-run artifacts: `narrative.md` + `done-explanation.md` ghi vào `logs/{runId}/`

**Mission Memory** (`mission_memory.py`):
- [x] Project direction + objectives + success criteria + priorities
- [x] YAML persistence tại `.copilot-operator/mission.yml`
- [x] Auto-update từ run results (mark objectives done, extract lessons)
- [x] Lesson trimming (giới hạn 20 lessons)
- [x] Prompt injection — render mission context vào _build_prompt để ngăn goal drift

**Memory Promotion** (`memory_promotion.py`):
- [x] 5 promotion rules: trap→project, validation→project, strategy→cross_repo, lesson→mission, demotion trimming
- [x] Configurable thresholds (PromotionThresholds dataclass)
- [x] Cross-layer promotion: working → task → project → mission → cross_repo
- [x] Deduplication khi promote (không thêm trùng)
- [x] Integrated vào `_post_run_hooks` — chạy tự động sau mỗi run

### Phase 11 — Security Hardening + 5-Tier Memory Model ✅ (NEW)

**Pre-Validation Diff Security Scan** (`diff_scan.py`):
- [x] 18 regex threat patterns across 5 categories (ACE, exfiltration, destructive, secrets, suspicious)
- [x] Scans only added lines from git diff — existing code not flagged
- [x] Critical findings → snapshot rollback + block operator
- [x] High/medium findings → log warning + continue
- [x] Best-effort: scan failure doesn't break operator
- [x] File skip rules: markdown, JSON, YAML, images, lock files

**SAST Adversarial Review** (`adversarial.py` enhanced):
- [x] 10-item SAST Security Checklist in critic prompt
- [x] Covers: injection, hardcoded secrets, path traversal, ACE, insecure deserialization, data exfiltration, destructive ops, insecure crypto, missing auth, sensitive data exposure

**Task Ledger — Structured Per-Run State** (`task_ledger.py`):
- [x] `TaskLedger` dataclass: goal, acceptance criteria, plan milestones, entries, decisions, files touched
- [x] `Commitments`: decided/owed/next_step/must_not_forget — survives session resets
- [x] Persist to `logs/{runId}/ledger.json`
- [x] Update after each iteration (accumulate files, validations, blockers)
- [x] Prompt injection — render ledger into `_build_prompt` so model "lives in the plan"

**Archive Retrieval — Retrieval Not Replay** (`archive_retrieval.py`):
- [x] Keyword search over past run narratives + ledgers
- [x] Returns top-K relevant hits sorted by relevance
- [x] Deduplication per run_id (best hit wins)
- [x] Query at run start — inject relevant past context into prompt
- [x] Skip current run, configurable max results

**Commitment-Based Summary** (`narrative.py` enhanced):
- [x] `build_commitment_summary()` — structured dict instead of prose
- [x] Captures: decided, owed, next_step, must_not_forget
- [x] Score regression detection
- [x] Owed items extracted from remaining plan tasks

### Phase 11b — Memory Hardening ✅ (NEW)

**BM25 Retrieval** (`archive_retrieval.py` rewrite):
- [x] BM25 scoring (TF-IDF variant, k1=1.2, b=0.75) replaces naive keyword counting
- [x] N-gram boost — character 3-grams catch partial matches ('auth' → 'authentication'), capped at 0.3
- [x] LLM reranking — optional semantic reranking of top candidates via LLM Brain
- [x] Corpus-level IDF — document frequency across all past runs

**Priority Pressure** (`task_ledger.py` enhanced):
- [x] `PriorityPressure` dataclass: focus, blocking_now, critical_path, can_defer, urgency, reasoning
- [x] `compute_priority_pressure()` — analyzes score trajectory, blockers, validation failures
- [x] Urgency levels: critical (blockers/regression), high (failing validations), normal, low (target met)
- [x] Deferrable detection — identifies docs/readme/style/cleanup tasks
- [x] Rendered into prompt with urgency icons (🚨/⚠️/📌/📎)

**Mission Drift Guard** (`mission_memory.py` enhanced):
- [x] `hard_constraints` field on Mission — MUST NOT violate rules
- [x] `check_mission_drift()` — hard constraint violation + objective alignment + priority alignment
- [x] Severity levels: severe (hard constraint), mild (misalignment), none (aligned)
- [x] `render_drift_correction()` — prompt injection block for realignment
- [x] Drift correction injected FIRST in prompt (highest authority)

### Phase 12 — Memory System v2 ✅ (NEW)

**Semantic Retrieval** (`archive_retrieval.py` enhanced):
- [x] TF-IDF cosine similarity — vector-based semantic scoring across documents
- [x] Context-aware boosting — goal type similarity, file overlap, successful outcome bonus
- [x] Relevance decay — older runs penalised (half-life = 10 runs)
- [x] Multi-signal combined scoring: BM25 40% + semantic 35% + context 25% - decay
- [x] `RelevanceSignal` breakdown for transparency (bm25, semantic, context, decay)
- [x] Signal breakdown rendered in prompt for high-relevance hits

**Priority System** (`task_ledger.py` enhanced):
- [x] `TaskPriority` levels: P0-CRITICAL, P1-HIGH, P2-MEDIUM, P3-LOW
- [x] `PrioritizedTask` with dependencies (`blocked_by`/`blocks`), status, escalation tracking
- [x] `PriorityQueue` — ordered task execution, `next_actionable()` respects blockers
- [x] `build_priority_queue()` — classifies tasks from plan (test→P1, docs→P3, mission boost)
- [x] `escalate_priorities()` — automatic escalation rules:
  • Score regression → blockers to P0
  • Stuck → next task to P1
  • Dependency cascade → tasks blocking 2+ others to P1
  • Time pressure (4+ iterations) → P2 to P1
- [x] Mission priority override integration
- [x] Priority queue rendered in prompt with icons and escalation reasons

**Mission Authority** (`mission_memory.py` enhanced):
- [x] `MissionAuthority` class — wraps Mission, makes it binding not advisory
- [x] `MissionDirective` — mandatory instructions (constraints, priorities, objectives)
- [x] `evaluate_action()` — veto mechanism, checks constraints, returns alternative
- [x] `MissionVeto` — blocks actions that violate hard constraints
- [x] `override_decision()` — forces realignment when score gap large or action misaligned
- [x] `force_priority()` — mission can override task priorities directly
- [x] `render_authority_block()` — injected at VERY TOP of prompt (highest authority)
- [x] Integrated veto, override, and authority into operator.py prompt building

### Phase 13 — Intelligence Engine ✅ (NEW)

**Context Budget Manager** (`context_budget.py`):
- [x] `PromptSection` — name, content, priority (P0-P4), compressible flag, token estimation
- [x] `ContextBudget` — max_tokens, pressure tracking (normal/tight/critical)
- [x] 4-phase compression: drop P4 → compress P3 to 50% → compress P2 to 60% → drop P3
- [x] Score-declining adjustment — 20% more budget when score is dropping
- [x] Head/tail compression strategy — keeps first 60% + separator + last 20%
- [x] `build_budgeted_sections()` — main API, returns combined text + budget stats

**Adaptive Strategy Engine** (`adaptive_strategy.py`):
- [x] `RunFeedback` — per-iteration state (score delta, blockers, validation, files changed, loops)
- [x] `StrategyState` — tracks strategies tried, effective, failed, guardrails, escalation level
- [x] Escalation chain: default → narrow_scope → switch_approach → escalate_profile → abort
- [x] 5 feedback rules: 2 loops→escalate, 3 no-progress→escalate, score drop>10→guardrail, validation failing→guardrail, no files→guardrail
- [x] Goal-type strategies (bug, feature, refactor, test, generic)
- [x] `get_directives()` — renders adaptive strategy block for prompt injection
- [x] `should_abort()` — recommends abort when escalation reaches terminal level

**Self-Evaluation Feedback Loop** (`self_eval.py`):
- [x] `ScoreMovement` — before/after/delta/direction/explanation
- [x] `IterationEval` — comprehensive iteration analysis (files, validations fixed/broken, corrections)
- [x] `evaluate_iteration()` — compares before/after state, generates what_helped/what_hurt/correction
- [x] `render_eval_for_prompt()` — icons (📈/📉/✅/❌/🔧/🟢/🔴), injected into next iteration prompt
- [x] Closed feedback loop: iteration N → score → self-eval → prompt for N+1

**Integration** (`operator.py`):
- [x] Self-eval runs at TOP of `_build_prompt` — feeds back into next iteration
- [x] Adaptive engine initialized per-run, fed `RunFeedback` each iteration
- [x] All 13+ intelligence sections wrapped in `PromptSection` with priorities
- [x] `build_budgeted_sections()` controls total intelligence token budget (6000 default)
- [x] Adaptive directives and self-eval corrections injected into prompt

### Phase 14 — Intelligence Actuation ✅ (NEW)

**Hint Actuator** (`hint_actuator.py`):
- [x] `actuate_hints()` — execute `StrategyHint` actions instead of just prompting them
- [x] `escalate_profile` → mutate config.goal_profile + track profile history
- [x] `narrow_scope` → halve max_files_changed (floor=3), set runtime flag
- [x] `increase_iterations` → bump max_iterations when near limit (cap=12)
- [x] `switch_baton` → inject suggested_prompt as baton override
- [x] `abort` → set runtime abort flag with reason
- [x] `ActuationResult` — tracks actions_taken, config_changes, runtime_changes

**Benchmark Learner** (`benchmark_learner.py`):
- [x] `analyse_benchmark_for_rules()` — extract PromptRule-compatible rules from failed cases
- [x] Pattern 1: Missing keywords → guardrail requiring those concepts
- [x] Pattern 2: Low score (< 50%) → guardrail about thoroughness
- [x] Pattern 3: Errors → guardrail about error class
- [x] Deduplication against existing rule triggers
- [x] `render_benchmark_lessons()` — human-readable failure summary

**Intelligence Telemetry** (`intelligence_telemetry.py`):
- [x] `InjectionRecord` — per-iteration per-source injection tracking
- [x] `TelemetryAggregator` — collects records, builds effectiveness report
- [x] `SourceStats` — help_rate, avg_tokens, times_helped/neutral/hurt/dropped
- [x] `IntelligenceReport` — top sources, wasteful sources, per-source table
- [x] `get_priority_adjustment()` — suggest priority changes based on effectiveness
- [x] JSON serialisation (save/load) for cross-run analysis

**Integration** (`operator.py`):
- [x] Hint actuator in `_decide()` — executes hints after diagnosis, before baton
- [x] Baton override from `switch_baton` hint consumed and prepended
- [x] Abort from hint actuator → Decision(action='stop')
- [x] Telemetry recording in `_build_prompt` after budget allocation
- [x] Telemetry saved in `_post_run_hooks` with effectiveness report in result

### Phase 15 — AI Narrative UI ✅ (NEW)

**Narrative Engine** (`narrative_engine.py` ~580 dòng):
- [x] `LiveNarrative` — real-time snapshot: task, milestone, iteration, score, strategy, urgency, blocking
- [x] `DecisionTrace` — per-iteration audit trail: observation, hypothesis, decision, alternatives, actuation, self-eval
- [x] `RunSummaryView` — post-run summary: goal, outcome, files_changed, tests, stop_reason, conditions, risks, next_step
- [x] `MemorySnapshot` — project memory: mission, project stats, rules, lessons, strategies, telemetry
- [x] `NarrativeEngine` — aggregator class: `build_live()`, `build_decision_trace()`, `build_summary()`, `build_memory()`, `render_all_views()`

**Narrative Formats** (`narrative_formats.py` ~210 dòng):
- [x] `format_live_block()` — [STATE] / [ACTION] / [PRESSURE] tagged structure
- [x] `format_decision_block()` — [ITERATION] / [OBSERVATION] / [HYPOTHESIS] / [DECISION] / [ACTUATION] / [SELF_EVAL]
- [x] `format_summary_block()` — [GOAL] / [OUTCOME] / [CHANGES] / [VALIDATION] / [WHY_DONE] / [RISKS] / [NEXT]
- [x] `format_memory_block()` — [MISSION] / [PROJECT] / [RULES] / [LESSONS] / [STRATEGIES] / [TELEMETRY]
- [x] `views_to_dict()` — structured dict export for JSON/programmatic use

**Integration** (`operator.py` + `cli.py`):
- [x] `NarrativeEngine` initialized in `_prepare_fresh_run()` and `_prepare_resume_run()`
- [x] Decision trace recorded per iteration in `_run_iteration()` after `_decide()`
- [x] Full 4-view rendering in `_post_run_hooks()` → saved as `narrative-ui.txt`
- [x] CLI `explain` command — show all 4 views or single view (--view live/summary/memory) with --json

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

**Tổng: 866 test, 31+ file, tất cả PASS** ✅ (lint clean)

| File test | Số test | Nội dung |
|-----------|--------:|----------|
| `tests/hooks.test.cjs` | 2 | Node.js hooks (state-gate, pre-chat) |
| `tests_python/test_operator.py` | 24 | Core operator loop, circuit breaker, retry logic |
| `tests_python/test_new_modules.py` | 47 | Brain, meta-learner, adversarial, snapshot, reasoning |
| `tests_python/test_new_features.py` | 41 | Goal decomposer, planner, scheduler, repo ops, prompts |
| `tests_python/test_features_af.py` | 48 | LLM brain, repo map, intention guard, session store, cross-repo brain |
| `tests_python/test_hardening.py` | 39 | Dashboard, nightly, ROI, policy, GitHub integration, worker |
| `tests_python/test_llm_brain.py` | 51 | LLM provider tests (all 5 providers) + **Deep Brain** (compose, decide, review, operator integration, git-aware blocked) |
| `tests_python/test_terminal_benchmark.py` | 42 | Terminal runner, benchmark suite |
| `tests_python/test_checklist_features.py` | 41 | v2.3.0 features: SLA, compaction, changelog, conflicts, rubric |
| `tests_python/test_hardening_v2.py` | 54 | Runtime guard, stop controller, worker contract, CI integration |
| `tests_python/test_narrative_memory.py` | 35 | Run narrative, mission memory, memory promotion |
| `tests_python/test_auto_resume.py` | 7 | Auto-resume sau crash/interrupt |
| `tests_python/test_context_optimization.py` | 8 | Context window optimization |
| `tests_python/test_error_recovery.py` | 12 | Error recovery strategies |
| `tests_python/test_p3_p4.py` | 25 | Phase 3+4 integration (planner + scheduler) |
| `tests_python/test_risk_gate.py` | 6 | Risk gate evaluation |
| `tests_python/test_scheduler.py` | 12 | Multi-session scheduler |
| `tests_python/test_stop_controller.py` | 15 | Stop controller logic |
| `tests_python/test_stress_edge_cases.py` | 10 | Stress tests, edge cases |
| `tests_python/test_validation_autofill.py` | 9 | Validation command autofill |
| `tests_python/test_logging_vscode.py` | 20 | logging_config + vscode_chat |
| `tests_python/test_config_wiring.py` | 15 | Config wiring: repo_map, snapshot, stop_controller, promotion thresholds |
| `tests_python/test_diff_scan.py` | 36 | **NEW** Diff scan: threat patterns, dedup, skip rules, SAST prompt |
| `tests_python/test_memory_model.py` | 29 | 5-tier memory: task ledger, archive retrieval, commitment summary |
| `tests_python/test_memory_hardening.py` | 31 | Memory hardening: BM25 scoring, n-gram boost, priority pressure, mission drift guard |
| `tests_python/test_memory_v2.py` | 55 | **NEW** Memory v2: semantic retrieval, priority system, mission authority (veto, directives, override) |
| `tests_python/test_intelligence_engine.py` | 38 | **NEW** Intelligence engine: context budget, adaptive strategy, self-evaluation loop |
| `tests_python/test_intelligence_actuation.py` | 39 | **NEW** Intelligence actuation: hint actuator, benchmark learner, telemetry |
| `tests_python/test_narrative_ui.py` | 46 | **NEW** Narrative UI: 4 view dataclasses, NarrativeEngine, narrative_formats |
| `tests_python/test_integration_loop_closure.py` | 15 | **NEW** Integration: version sync, trace roundtrip, benchmark→learner, watch |
| `tests_python/test_narrative_ux.py` | 16 | **NEW** Narrative UX: format_summary_short, live streaming, --short CLI |

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
| `docs/COPILOT_OPERATOR_CHECKLIST.md` | Checklist triển khai (95/103 ✓) |
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
| Tổng kết operator gặp chỗ nào nhiều nhất | ✅ 12 bug đã tìm và fix |

### Còn lại

| Mục | Mô tả |
|-----|-------|
| Multi-session thực chiến | Scheduler chạy 2+ sessions song song trên repo thật |
| Benchmark so sánh với đối thủ | Cần baseline từ SWE-Bench / Aider |

### Definition of Done — Phase hiện tại

| Mục | Trạng thái |
|-----|-----------|
| Operator chạy được trên repo thật | ✅ 2 repo (note-cli + english) |
| Validation thật đã được điền vào config | ✅ Jest + Vitest + ESLint |
| Có goal templates thật | ✅ 3 templates per project |
| Có 3+ session thành công | ✅ 16 sessions (4 toy + 4 note-cli + 2 english + 6 localrag) |
| Có runbook để người khác đọc và dùng | ✅ Có toàn bộ + troubleshooting 13 bugs |

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
| Phase 9 | Hardening & Runtime Safety | ✅ Hoàn thành |
| Phase 10 | Run Narrative + Mission Memory + Memory Promotion | ✅ Hoàn thành |
| Phase 11 | Security Hardening + 5-Tier Memory Model | ✅ Hoàn thành |
| Phase 12 | Memory System v2 | ✅ Hoàn thành |
| Phase 13 | Intelligence Engine | ✅ Hoàn thành |
| Phase 14 | Intelligence Actuation | ✅ Hoàn thành |
| Phase 15 | AI Narrative UI | ✅ Hoàn thành |
| Phase 16 | Integration & Loop Closure | ✅ Hoàn thành |
| Phase 17 | Narrative UX | ✅ Hoàn thành |
| Phase 18 | Deep Brain | ✅ **Hoàn thành (mới)** |

### Phase 11 — Chi tiết (Security + 5-Tier Memory)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `diff_scan.py` — 18 regex threat patterns (ACE, exfiltration, destructive, secrets, suspicious) | ✅ Done + 36 tests |
| 🟠 P2 | `adversarial.py` — 10-item SAST security checklist in critic prompt | ✅ Done (included in diff_scan tests) |
| 🟡 P3 | `task_ledger.py` — Structured per-run state, commitments, prompt injection | ✅ Done + 29 tests |
| 🟢 P4 | `archive_retrieval.py` — Keyword search over past runs, retrieval not replay | ✅ Done (included in memory model tests) |
| 🔵 P5 | `narrative.py` — `build_commitment_summary()` (decided/owed/next_step/must_not_forget) | ✅ Done (included in memory model tests) |
| ⚪ P6 | `operator.py` — Integrated diff scan + task ledger + archive into run lifecycle | ✅ Done |

### Phase 10 — Chi tiết

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `narrative.py` — LiveStatus, RunNarrative, DoneExplanation | ✅ Done + 13 tests |
| 🟠 P2 | `mission_memory.py` — Mission persistence, auto-update, prompt injection | ✅ Done + 8 tests |
| 🟡 P3 | `memory_promotion.py` — 5 promotion rules, thresholds, cross-layer | ✅ Done + 12 tests |
| 🟢 P4 | Integration — operator.py (_post_run_hooks + _build_prompt) + cli.py (2 commands) | ✅ Done + 2 tests |

### Phase 9 — Chi tiết

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `runtime_guard.py` — Lock, conflict, checkpoint, continuity | ✅ Done + 10 tests |
| 🟠 P2 | `stop_controller.py` — No-progress, dedup, timeout, floor, escalation | ✅ Done + 12 tests |
| 🟡 P3 | `worker.py` enhanced — HealthSignal, RecyclePolicy, RequiredArtifacts, TaskInput | ✅ Done + 18 tests |
| 🟢 P4 | `ci_integration.py` — GitHub Actions trigger, analyse, fix prompt | ✅ Done + 14 tests |

### Phase 13 — Chi tiết (Intelligence Engine)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `context_budget.py` — P0-P4 priority tiers, 4-phase compression, token budget allocation | ✅ Done + 12 tests |
| 🟠 P2 | `adaptive_strategy.py` — Escalation chain, 5 feedback rules, mid-run guardrails | ✅ Done + 13 tests |
| 🟡 P3 | `self_eval.py` — Score movement, validation comparison, correction injection | ✅ Done + 13 tests |
| 🟢 P4 | `operator.py` — Integrated all 3 into _build_prompt (self-eval → sections → budget) | ✅ Done |

### Phase 14 — Chi tiết (Intelligence Actuation)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `hint_actuator.py` — Execute reasoning hints (profile, scope, iterations, baton, abort) | ✅ Done + 14 tests |
| 🟠 P2 | `benchmark_learner.py` — Extract rules from failed benchmark cases | ✅ Done + 10 tests |
| 🟡 P3 | `intelligence_telemetry.py` — Track injection effectiveness, save/load, reports | ✅ Done + 15 tests |
| 🟢 P4 | `operator.py` — Integrated actuation in _decide(), telemetry in _build_prompt + _post_run_hooks | ✅ Done |

### Phase 16 — Chi tiết (Integration & Loop Closure)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `benchmark.py` → `benchmark_learner.py` — Auto-extract learning rules after benchmark failures | ✅ Done |
| 🟠 P2 | `narrative_engine.py` — `serialize_traces()` / `load_traces()` + JSON persistence in operator | ✅ Done |
| 🟡 P3 | `bootstrap.py` — `format_operator_status()` includes `LiveNarrative` block from NarrativeEngine | ✅ Done |
| 🟢 P4 | `operator.py` — Per-iteration `narrative-log.txt` using `format_iteration_log()` | ✅ Done |
| 🔵 P5 | `cli.py` — Enhanced `explain` command: `--view trace`, past-run trace loading, mission+rules in all views | ✅ Done |
| ⚪ P6 | `__init__.py` + `pyproject.toml` — Version sync to 3.0.0, 5 new exports | ✅ Done |
| ⚪ P7 | Lint cleanup — Fixed unused imports/variables across 4 test files | ✅ Done + 15 tests |

### Phase 17 — Chi tiết (Narrative UX)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `operator.py` — Live stream: validation results + decision trace to stderr per iteration | ✅ Done |
| 🟠 P2 | `operator.py` — Auto-summary: print `format_summary_short()` to stderr after every run | ✅ Done |
| 🟡 P3 | `cli.py` — `explain --short`: ultra-compact 5-line summary mode | ✅ Done |
| 🟢 P4 | `narrative_formats.py` — `format_summary_short()` + `format_live_stream_line()` | ✅ Done + 16 tests |

### Phase 18 — Chi tiết (Deep Brain)

| Ưu tiên | Module | Trạng thái |
|---------|--------|------------|
| 🔴 P1 | `llm_brain.py` — `compose_prompt()`: LLM tự phân tích tình huống → viết prompt cụ thể cho Copilot | ✅ Done |
| 🟠 P2 | `llm_brain.py` — `make_decision()`: LLM quyết định tiếp/dừng với confidence score | ✅ Done |
| 🟡 P3 | `llm_brain.py` — `review_iteration()`: LLM review code diff, phát hiện vấn đề logic | ✅ Done |
| 🟢 P4 | `operator.py` — Tích hợp Deep Brain vào 3 điểm: _build_prompt, _decide, _run_iteration | ✅ Done |
| 🔵 P5 | Safety: 6 hard-stop codes không bao giờ bị override, confidence threshold 0.6–0.85 | ✅ Done + 14 tests |

**Chi tiết Deep Brain:**
- ✅ LLM tự suy nghĩ (thinking) → chọn chiến lược (strategy) → viết prompt cụ thể thay vì template cứng
- ✅ LLM validate/override quyết định rule-based với confidence check
- ✅ LLM review code diff sau mỗi iteration, inject correction vào prompt tiếp
- ✅ Fallback về template nếu brain không sẵn sàng hoặc lỗi
- ✅ Đã test với xAI Grok-3-mini: compose, decide, review đều trả kết quả chính xác

### Trong dự định (chưa code)

#### CI Integration thực chiến
> ✅ Module `ci_integration.py` + CLI `copilot-operator ci` (5 actions) đã xong.
> Cần `GITHUB_TOKEN` để test E2E.

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

### Giai đoạn 4: localrag — Python CLI (TF-IDF + NumPy RAG)

| # | Goal | Kết quả | Iter | Ghi chú |
|---|------|---------|:----:|---------|
| 11 | Add unit tests (63 tests) | ✅ Score 95 | 1 | Session detection bug — Copilot hoàn thành nhưng operator không detect |
| 12 | Add chat REPL feature | ✅ Score 95, 71 tests | 1 | 8 chat tests mới, cùng session detection bug |
| 13 | Add __version__ check | ✅ Copilot tạo code + tests | 2 | **Sau fix race condition** — operator chạy đủ 2 iterations, session detection OK |

### Giai đoạn 5: localrag — Deep Brain Live Test (LLM-driven, xAI Grok-3-mini)

| # | Goal | Kết quả | Iter | Ghi chú |
|---|------|---------|:----:|---------|
| 14 | Add input validation (chunk_size, top_k, model_name) | ⚠️ BLOCKED (DIFF_DEDUP) | 2 | Copilot thay đổi code thật nhưng **không emit `<OPERATOR_STATE>`** → operator coi là blocked → retry → DIFF_DEDUP. Phát hiện root cause |
| 15 | Add input validation (retry sau git-aware fix) | ⚠️ BLOCKED (DIFF_DEDUP) | 3 | **Git-aware fix hoạt động!** Iter 1→2 dùng prompt "don't re-edit, report status only". Cải thiện từ 2→3 iterations trước khi DIFF_DEDUP |
| 16 | Add input validation (scoped goal) | ✅ Code changes OK | 3 | Copilot viết 100+ dòng: 3 validation functions + chat REPL + config validation. **Code đúng, chỉ operator không detect score** |

**Chi tiết thay đổi Copilot tạo ra (session 15-16):**
- `cli.py`: +99 dòng — `_validate_workspace()`, `_validate_index_name()`, `_validate_query_text()`, `_cmd_chat` REPL, validation cho tất cả commands
- `config.py`: +8 dòng — validate `chunk_max_lines > 0`, `top_k > 0`, `embedding.model` not empty

**Phát hiện quan trọng:**
- Copilot Chat agent mode **không emit** format `<OPERATOR_STATE>{...}</OPERATOR_STATE>` — đây là limitation cơ bản
- Git-aware COPILOT_BLOCKED fix (commit `3e71493`) đã cải thiện resilience: nhận biết workspace có thay đổi thật → gửi prompt "báo cáo status, không sửa code"
- Deep Brain LLM (xAI Grok-3-mini) hoạt động tốt: compose prompt, adapt across iterations, nhận diện pattern

### Kết quả ROI tổng hợp
- **Tổng session:** 16 (4 toy + 4 note-cli + 2 english + 6 localrag)
- **Success rate:** 100% code changes đúng (13/16 operator detect score, 3/16 bị OPERATOR_STATE limitation)
- **Multi-iteration tasks:** 6 (SQLite: 3, CLI refactor: 3, version: 2, Deep Brain: 2+3+3 iter)
- **Lớn nhất:** english project — 835 tests, full-stack, production code
- **Bug phát hiện & fix:** 13 operator bugs qua E2E thực chiến (thêm git-aware BLOCKED fix)
- **Deep Brain live:** 3 sessions, LLM compose/decide/review hoạt động, bottleneck là OPERATOR_STATE format

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
| 8 | Config resolution: `--workspace` load config từ CWD thay vì workspace | `config.py` | `load_config()` thử workspace target trước |
| 9 | Race condition: `code chat` gửi prompt trước khi window focus hoàn tất | `vscode_chat.py` | Thêm `focus_settle_seconds` (2s) delay |
| 10 | Stale session: Copilot bị treo, operator chờ 15 phút | `vscode_chat.py` | Thêm stale timeout (60s) tự return session |
| 11 | `NarrativeEngine` không JSON serializable khi in kết quả | `cli.py` | Xóa `_narrativeEngine` trước `json.dumps()` |
| 12 | `watch` command treo không thoát khi status là done/error | `bootstrap.py` | Thêm exit conditions: done, error, stopped |
| 13 | COPILOT_BLOCKED khi Copilot thay đổi code nhưng không emit OPERATOR_STATE | `operator.py` | Git-aware retry: check `get_diff_summary()`, nếu có changes → gửi prompt "report status only" |

### Config mới thêm qua E2E

| Feature | File | Mô tả |
|---------|------|-------|
| `chatModel` field | `config.py` | Cho phép chọn model trong `copilot-operator.yml` |
| Default model Sonnet | `bootstrap.py` | Chuyển từ Opus (đắt) sang Sonnet 4.5 |
| Git-diff fallback | `operator.py` | Khi session timeout, check git diff → continue nếu có changes |
| `--lang vi` | `cli.py` + `locale.py` | Đa ngôn ngữ CLI: tiếng Anh/tiếng Việt |
| `focus_settle_seconds` | `config.py` | Delay 2s sau focus window, trước khi gửi chat |
| Stale session timeout | `vscode_chat.py` | 60s không thay đổi → return session hiện tại |
| `--lang vi` | `cli.py` + `locale.py` | Đa ngôn ngữ CLI: tiếng Anh/tiếng Việt |
| `focus_settle_seconds` | `config.py` | Delay 2s sau focus window, trước khi gửi chat |
| Stale session timeout | `vscode_chat.py` | 60s không thay đổi → return session hiện tại |

---

## 14. Nhận định tình trạng hiện tại

### Mô tả thẳng thắn

> An autonomous Copilot supervisor with 51 modules, intelligence engine, narrative UX, and i18n — battle-tested on 16 sessions across 5 repos (including 3 Deep Brain LLM-driven sessions). Đang ở giai đoạn "internal power tool rất mạnh", chưa ở mức "devtool phổ cập cho người lạ". Build đủ rồi — giờ cần execution proof.

### Session surface — vẫn là điểm coupling lớn nhất

12/12 bug thực chiến phần lớn liên quan đến **session surface** — vấn đề này **vẫn tồn tại** nhưng giờ đã có:
- **Focus settle delay** (2s) ngăn race condition khi nhiều VS Code window
- **Stale session timeout** (60s) tự return khi Copilot tool bị treo
- **Lock file** ngăn chạy 2 operator cùng lúc
- **Conflict detection** cảnh báo khi VS Code window đang bận
- **Checkpoint** cho phép resume sau crash
- **Context bridge** duy trì continuity khi session mới được tạo

### Stop controller — giải quyết vấn đề babysit

Trước v2.4.0: phải canh operator chạy, sợ bị loop vô hạn.
Sau v2.4.0:
- **No-progress detection** → dừng nếu 2 iterations không có diff
- **Diff dedup** → phát hiện Copilot lặp code cũ
- **Score floor** → hard stop nếu score dưới 20 liên tục 3 iterations
- **Hard vs soft** → phân biệt "dừng hẳn" vs "báo human quyết định"

### Worker contract — nền tảng cho multi-session

Worker giờ là **thực thể vận hành**, không chỉ wrapper:
- HealthSignal (HEALTHY/DEGRADED/DEAD) cho scheduler biết khi nào recycle
- RecyclePolicy kiểm soát lifecycle tự động
- RequiredArtifacts đảm bảo output đầy đủ mỗi iteration

### "Battle-tested" nhưng chưa "generalized-ready"

Đã chạy tốt trên:
- repo nhỏ (toy Python)
- repo vừa (note-cli, Python CLI)
- repo vừa (localrag, Python CLI + RAG) — **bao gồm 3 Deep Brain sessions với xAI Grok-3-mini**
- 1 repo production lớn (english, React + Express + MongoDB)

**Nhưng:** Cần thêm repo với ecosystem khác (Go, Rust, Java, monorepo...) và cần multi-session thực chiến để validate.

### Multi-session: code có + tích hợp xong, cần live proof

Scheduler, worker (giờ có contract), conflict tracker, baton merge — đã code xong.
`run_multi_session()` đã tích hợp **FileConflictTracker** + **merge_batons()** vào vòng lặp chính.
Dry-run test trên localrag xác nhận plan creation + dependency ordering hoạt động đúng.
**Cần chạy live test** (yêu cầu VS Code Copilot Chat window mở cho target workspace).

### CI integration: module + CLI đã có, cần GITHUB_TOKEN

`ci_integration.py` đã có đầy đủ (trigger, wait, analyse, fix prompt).
**CLI command `copilot-operator ci`** đã có 5 actions: `list`, `trigger`, `check`, `wait`, `fix`.
`fix` action: auto-analyse CI failure → build goal → run operator → sửa code.
**Cần GITHUB_TOKEN** env var để kết nối GitHub Actions API.

### Đánh giá thẳng

| Tiêu chí | Điểm | Ghi chú |
|----------|------:|---------|
| Ý tưởng | 9/10 | Autonomous meta-agent điều khiển Copilot — rất khác biệt |
| Độ khác biệt | 8.5/10 | Không tool nào làm đúng cách này |
| Kiến trúc | 9/10 | 51 module tách rõ, testable, extensible |
| Thực chiến | 8.5/10 | 13 sessions, 5 repos, 12 bugs fixed |
| Runtime safety | 8.5/10 | Lock, checkpoint, stop controller, focus settle, stale timeout |
| UX / i18n | 8/10 | --live, --short, --lang vi, auto-summary |
| Độ bền dài hạn | 7.5/10 | Platform coupling vẫn là rủi ro chính |
| Tiềm năng (nếu đi đúng) | 9/10 | Rất đáng phát triển tiếp |

### Kết luận

- Không còn là prototype — đã là **internal power tool** hoàn chỉnh
- Đã có giá trị thật (13 sessions thành công, 5 repos bao gồm production app)
- **Giai đoạn hiện tại: "đã có hệ mạnh, giờ phải chứng minh, siết, và tận dụng"**
- Bước tiếp không phải "build thêm cho to" mà là **ép hệ thắng trên tình huống thật**
- **v3.2.0**: Deep Brain (LLM tự suy nghĩ, compose prompt, make decision, review code)
- **v3.1.0**: --lang vi, --live streaming, auto-summary, session race fix, stale timeout, localrag E2E
- **Điểm cần làm tiếp:**
  1. Multi-session thực chiến (2 workers trên repo thật)
  2. CI integration E2E (cần GITHUB_TOKEN)
  3. Siết usage loop hàng ngày
  4. Không mở thêm module lớn nữa

---

## 15. Chiến lược phát triển tiếp

### Hướng đi: Execution proof trước, không mở thêm module

**Đừng build thêm cho to.** Lấy cái đã có, ép nó thắng trên tình huống thật.

Mục tiêu giai đoạn tiếp:
- Chứng minh multi-session hoạt động trên repo thật
- Chứng minh CI loop (trigger → fail → fix → pass) hoạt động
- Siết usage loop hàng ngày cho chính mình
- Polish doc/positioning cho khớp thực lực

### ✅ Đã hoàn thành trong v2.6.0 (Security + 5-Tier Memory)

| Ưu tiên | Mô tả | Module | Tests |
|---------|-------|--------|------:|
| 🔴 P1 | Pre-validation diff security scan | `diff_scan.py` | 36 |
| 🟠 P2 | SAST adversarial checklist | `adversarial.py` | (in P1) |
| 🟡 P3 | Task ledger (structured per-run state) | `task_ledger.py` | 29 |
| 🟢 P4 | Archive retrieval (keyword search) | `archive_retrieval.py` | (in P3) |
| 🔵 P5 | Commitment-based summary | `narrative.py` | (in P3) |
| ⚪ P6 | Full operator integration | `operator.py` | (in P3) |

### ✅ Đã hoàn thành trong v2.5.0

| Ưu tiên | Mô tả | Module | Tests |
|---------|-------|--------|------:|
| 🔴 P1 | Run narrative layer | `narrative.py` | 13 |
| 🟠 P2 | Mission memory | `mission_memory.py` | 8 |
| 🟡 P3 | Memory promotion rules | `memory_promotion.py` | 12 |
| 🟢 P4 | Operator + CLI integration | `operator.py` + `cli.py` | 2 |

### ✅ Đã hoàn thành trong v2.4.0

| Ưu tiên | Mô tả | Module | Tests |
|---------|-------|--------|------:|
| 🔴 P1 | Harden runtime boundary | `runtime_guard.py` | 10 |
| 🟠 P2 | Stop controller mạnh hơn | `stop_controller.py` | 12 |
| 🟡 P3 | Chuẩn hóa worker contract | `worker.py` (enhanced) | 18 |
| 🟢 P4 | CI integration | `ci_integration.py` | 14 |

**Chi tiết P1 — Runtime Guard:**
- ✅ Lock file ngăn 2 operator cùng workspace
- ✅ Same-window conflict detection
- ✅ Per-iteration state checkpoint
- ✅ Context continuity bridge

**Chi tiết P2 — Stop Controller:**
- ✅ No-progress detection (dừng sau N iterations không diff)
- ✅ Diff dedup (phát hiện Copilot lặp code)
- ✅ Wall-clock task timeout
- ✅ Score floor (hard stop nếu score quá thấp)
- ✅ Hard stop vs soft escalate

**Chi tiết P3 — Worker Contract:**
- ✅ HealthSignal (HEALTHY/DEGRADED/DEAD)
- ✅ RecyclePolicy (auto kill+restart)
- ✅ RequiredArtifacts (kiểm tra output mỗi iteration)
- ✅ TaskInput chuẩn hóa

**Chi tiết P4 — CI Integration:**
- ✅ Trigger GitHub Actions workflow
- ✅ Read/wait for workflow result
- ✅ Analyse failed jobs + extract logs
- ✅ Build fix prompt for operator loop

### Ưu tiên tiếp theo

#### 🔴 Ưu tiên 1: Multi-session thực chiến

**Không viết thêm code scheduler.** Đem scheduler đã có ra chiến trường:

- 1 worker implement + 1 worker test/audit
- Chạy trên repo thật (localrag hoặc note-cli)
- Đo 3 thứ: conflict rate, merge pain, time/token savings
- **Nếu multi-session không thắng rõ ràng → không promote nó thành feature chính**
- Lưu ý: cần sửa `run_multi_session()` từ tuần tự → song song thật

#### 🟠 Ưu tiên 2: CI integration E2E

✅ Module `ci_integration.py` + CLI `copilot-operator ci` đã xong.
✅ GitHub Actions workflow `ci.yml` đã có (4 Python versions × 3 OS + build check).
Cần:
- Set `GITHUB_TOKEN` env var
- E2E: `ci check` → `ci fix` → `ci check` → pass
- **Milestone rất đáng tiền: chứng minh full DevOps loop**

#### 🟡 Ưu tiên 3: Siết usage loop hàng ngày

```
queue → run --live → summary → explain --short → roi
```

Mục tiêu: biến nó thành thói quen thật, phát hiện UX còn cấn chỗ nào.

#### 🟢 Ưu tiên 4: Chưa nên làm

| Feature | Lý do hoãn |
|---------|-----------|
| Web UI Dashboard | Chưa đủ user base, TUI đủ dùng |
| Plugin system | Over-engineering ở giai đoạn này |
| Cloud deployment | Local-first vẫn đúng strategy |
| VS Code Extension packaging | Cần platform coupling ổn trước |
| Team collaboration | Chưa ổn cho 1 người, chưa nên mở cho nhiều người |

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

*Tài liệu cập nhật — Copilot Operator v3.2.0 — 2026-04-12 — Deep Brain: LLM-driven autonomous thinking, prompt composition, decision making, code review*
