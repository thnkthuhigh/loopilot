# Copilot Operator

**Autonomous meta-agent that drives GitHub Copilot Chat to complete coding tasks end-to-end.**

Copilot Operator doesn't write code itself — it *controls* GitHub Copilot Chat via the VS Code CLI, evaluates results, detects stuck loops, and adapts its strategy until the task is done.

---

## Features

| Feature | Description |
|---------|-------------|
| **Operator Loop** | Send prompt → parse response → evaluate score → decide continue/stop |
| **Intelligence Engine** | Trend analysis, loop detection, adaptive strategy hints |
| **Goal Decomposition** | Auto-classify goals (bug/feature/refactor/docs/audit/stabilize) and build milestone plans. LLM-powered when available. |
| **Validation** | Run real test/lint/build commands between iterations |
| **Meta-Learning** | Detect failure patterns, generate prompt guardrails for future runs |
| **Adversarial Review** | Coder + Critic self-review before accepting results |
| **Snapshot & Rollback** | Git stash snapshots each iteration; auto-rollback on score regression |
| **LLM Brain** | Connect to OpenAI, Anthropic, Gemini, or local models for deeper analysis |
| **Repo Map** | AST + regex codebase index injected into every prompt (15 languages) — like Aider |
| **GitHub Integration** | Auto-fetch issues, create PRs — all via REST API (stdlib only) |
| **Cross-Repo Brain** | Share learnings across repositories via `~/.copilot-operator/shared-brain/` |
| **Adaptive Guardrails** | Static + dynamic guardrails that evolve based on run history |
| **Benchmark Engine** | Run and score operator cases against keyword expectations |
| **Live Mode** | Colour-coded real-time iteration progress in the terminal (`--live`) |
| **Dry-Run Mode** | Generate prompts without VS Code interaction — safe for testing |
| **Error Recovery** | Retry on transient errors, stop on consecutive failures |
| **Circuit Breaker** | Rate-limit protection for LLM and GitHub API calls |

---

## Quick Start

### Requirements

- Python 3.10+
- VS Code with GitHub Copilot extension
- `code` CLI available in PATH

### Install

```bash
pip install copilot-operator
```

Or from source:

```bash
git clone https://github.com/copilot-operator/copilot-operator
cd copilot-operator
pip install -e .
```

### Setup

```bash
# Scaffold workspace config
copilot-operator init

# Pre-flight checks
copilot-operator doctor
```

### Run

```bash
# Autonomous run with a goal
copilot-operator run --goal "Fix the login timeout bug in auth.py"

# Live colour-coded progress
copilot-operator run --goal "Add pagination to API" --live

# Dry-run: see the generated prompt without executing
copilot-operator run --goal "Add pagination to API" --dry-run

# Resume a stopped or blocked run
copilot-operator resume

# Fix a specific GitHub issue
copilot-operator fix-issue --issue 42 --repo owner/repo

# Watch progress live
copilot-operator watch

# Run benchmark cases
copilot-operator benchmark --file benchmark.json
```

---

## LLM Brain (Optional)

Set environment variables or create `.env` in your workspace:

```bash
# OpenAI
COPILOT_OPERATOR_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Anthropic
COPILOT_OPERATOR_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Gemini
COPILOT_OPERATOR_LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# Local (Ollama, LM Studio, etc.)
COPILOT_OPERATOR_LLM_PROVIDER=local
```

Check status:

```bash
copilot-operator brain
copilot-operator brain --test "What is 2+2?"
```

---

## Benchmark

Create a `benchmark.json` to measure operator quality:

```json
{
  "name": "My project benchmark",
  "cases": [
    {
      "id": "fix-auth-bug",
      "goal": "Fix the login bug where tokens expire too early",
      "goal_profile": "bug",
      "expected_keywords": ["token", "expiry", "authentication"]
    },
    {
      "id": "add-readme-docs",
      "goal": "Update README with installation and usage sections",
      "goal_profile": "docs",
      "expected_keywords": ["README", "installation", "usage"]
    }
  ]
}
```

```bash
# Human-readable report
copilot-operator benchmark --file benchmark.json

# Machine-readable JSON output
copilot-operator benchmark --file benchmark.json --json
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   CLI (cli.py)                       │
├──────────┬──────────────────────────────────────────┤
│          │         CopilotOperator (operator.py)     │
│          │  ┌──────────────┐  ┌──────────────────┐  │
│  Config  │  │  run() loop  │  │  _decide() logic │  │
│   YAML   │  │  ↓ prompt    │  │  score gates     │  │
│  + .env  │  │  ↓ send      │  │  blocker checks  │  │
│          │  │  ↓ parse      │  │  replan triggers │  │
│          │  │  ↓ validate   │  │  rollback logic  │  │
│          │  │  ↓ decide     │  │  critic checks   │  │
│          │  └──────────────┘  └──────────────────┘  │
├──────────┼──────────────────────────────────────────┤
│  Intelligence Layer                                  │
│  ┌────────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ reasoning  │ │  brain   │ │  meta_learner    │  │
│  │ (trends,   │ │ (project │ │  (pattern detect,│  │
│  │  loops)    │ │  history)│ │   prompt rules)  │  │
│  └────────────┘ └──────────┘ └──────────────────┘  │
├──────────┼──────────────────────────────────────────┤
│  External Integrations                               │
│  ┌────────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ llm_brain  │ │ snapshot │ │ github_integration│  │
│  │ (4 LLM     │ │ (git     │ │ (issues, PRs)    │  │
│  │  providers)│ │  stash)  │ │                   │  │
│  └────────────┘ └──────────┘ └──────────────────┘  │
├──────────┼──────────────────────────────────────────┤
│  VS Code Bridge                                      │
│  ┌──────────────┐ ┌────────────┐ ┌──────────────┐  │
│  │ vscode_chat  │ │ session    │ │ validation   │  │
│  │ (CLI bridge) │ │ store      │ │ (subprocess) │  │
│  └──────────────┘ └────────────┘ └──────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## Configuration

### `copilot-operator.yml`

```yaml
workspace: .
mode: agent
goalProfile: default          # bug | feature | refactor | audit | docs | stabilize
maxIterations: 6
targetScore: 85
sessionTimeoutSeconds: 900

validation:
  - name: tests
    command: npm test
    required: true
  - name: lint
    command: npm run lint
    required: false

llm:
  provider: openai            # openai | anthropic | gemini | local
  model: gpt-4o
```

### `.copilot-operator/repo-profile.yml`

```yaml
repoName: my-project
summary: A Node.js REST API
standards:
  - Use TypeScript strict mode
  - All functions must have tests
priorities:
  - Test coverage > 80%
protectedPaths:
  - database/migrations/
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `copilot-operator doctor` | Pre-flight checks (VS Code, config, validations) |
| `copilot-operator init` | Scaffold config files and documentation |
| `copilot-operator run --goal "..."` | Start a new autonomous run |
| `copilot-operator run --goal "..." --live` | Run with colour-coded real-time progress |
| `copilot-operator run --goal "..." --dry-run` | Generate prompt without executing |
| `copilot-operator resume` | Resume from last checkpoint |
| `copilot-operator status` | Show current run state |
| `copilot-operator plan` | Show the current milestone plan |
| `copilot-operator focus` | Show what the operator is working on |
| `copilot-operator watch` | Live-poll run progress |
| `copilot-operator brain` | Show LLM brain status |
| `copilot-operator brain --test "..."` | Test LLM brain with a prompt |
| `copilot-operator fix-issue --issue N --repo owner/repo` | Fetch GitHub issue and run operator to fix it |
| `copilot-operator benchmark --file bench.json` | Run benchmark cases and score results |
| `copilot-operator cleanup` | Remove old run logs |
| `copilot-operator version` | Show version |
| `copilot-operator -V` | Short version flag |

---

## How It Works

1. **You provide a goal** — e.g., "Fix issue #42" or "Add pagination to the API"
2. **Operator classifies the goal** — determines it's a bug fix, feature, refactor, etc.
3. **Operator builds a milestone plan** — heuristic-based or LLM-powered
4. **For each iteration:**
   - Takes a git stash snapshot
   - Runs pre-validation (tests, lint)
   - Builds a rich prompt with context, guardrails, and intelligence
   - Sends the prompt to Copilot Chat via `code chat --mode agent`
   - Waits for and parses the response
   - Runs post-validation
   - Evaluates: score, blockers, validation results, trend
   - Decides: continue, replan, rollback, or stop
5. **After the run:**
   - Meta-learner extracts failure patterns as rules for future runs
   - Cross-repo brain exports learnings
   - Results saved for project brain analysis

---

## Project Structure

```
copilot_operator/
├── operator.py            # Main orchestration loop
├── vscode_chat.py         # VS Code CLI bridge (subprocess)
├── session_store.py       # VS Code session file parser
├── validation.py          # Test/lint/build command execution
├── config.py              # YAML config + .env loading
├── cli.py                 # CLI with 15+ subcommands
├── prompts.py             # Prompt templates + response parsing
├── planner.py             # Plan parsing, merging, rendering
├── reasoning.py           # Trend analysis, loop detection
├── brain.py               # Project history analysis
├── goal_decomposer.py     # Goal classification + LLM decomposition
├── repo_map.py            # AST + regex codebase map (15 languages)
├── benchmark.py           # Benchmark runner + scoring engine
├── terminal.py            # ANSI colour helpers (NO_COLOR compliant)
├── scheduler.py           # Multi-session orchestration
├── repo_ops.py            # Git operations (branch, commit, diff)
├── meta_learner.py        # Pattern detection + rule learning
├── adversarial.py         # Coder + Critic review
├── llm_brain.py           # Multi-provider LLM (circuit breaker)
├── github_integration.py  # GitHub REST API (retry + rate limit)
├── snapshot.py            # Git stash snapshots + rollback
├── cross_repo_brain.py    # Shared knowledge across repos
├── intention_guard.py     # Static + adaptive guardrails
├── repo_inspector.py      # Workspace ecosystem detection
├── bootstrap.py           # Workspace scaffolding
├── logging_config.py      # Structured logging
└── py.typed               # PEP 561 type marker
```

---

## License

MIT

## Docs index

Read these first if you want a working rollout plan:

- `docs/COPILOT_OPERATOR_MASTER_PLAN.md`: the long-range architecture and phase roadmap
- `docs/COPILOT_OPERATOR_CHECKLIST.md`: the actionable checklist, with current items marked done or pending
- `docs/COPILOT_OPERATOR_RUNBOOK.md`: day-0 setup, operating steps, and unblock flow
- `docs/COPILOT_OPERATOR_BACKLOG.md`: ticket-ready engineering backlog
- `docs/COPILOT_OPERATOR_GOAL_TEMPLATES.md`: reusable goal templates for real runs
- `docs/operator/`: seeded repo-brain files attached to the operator profile

## Validation

```bash
npm test
npm run operator:doctor
npm run supervisor:doctor
```

