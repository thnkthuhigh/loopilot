const fs = require("node:fs");
const path = require("node:path");

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(chunks.join("")));
    process.stdin.on("error", reject);
  });
}

async function readHookInput() {
  const raw = (await readStdin()).trim();
  return raw ? JSON.parse(raw) : {};
}

function writeJson(value) {
  process.stdout.write(JSON.stringify(value));
}

function repoRoot(input) {
  return path.resolve(input.cwd || process.cwd());
}

function sanitizeSessionId(sessionId) {
  return String(sessionId || "default-session").replace(/[^a-zA-Z0-9._-]/g, "_");
}

function stateDir(root) {
  return path.join(root, ".copilot", "supervisor");
}

function stateFilePath(root, sessionId) {
  return path.join(stateDir(root), `${sanitizeSessionId(sessionId)}.json`);
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function readJsonSubsetYaml(filePath, fallback) {
  if (!fs.existsSync(filePath)) {
    return fallback;
  }

  const raw = fs.readFileSync(filePath, "utf8").trim();
  if (!raw) {
    return fallback;
  }

  return JSON.parse(raw);
}

function defaultScorecard() {
  return {
    targetScore: 85,
    maxForcedContinuations: 5,
    weights: {
      tests: 30,
      requirements: 25,
      codeQuality: 20,
      ux: 10,
      market: 15
    },
    blockerSeverities: ["critical", "high"]
  };
}

function defaultBenchmarkConfig() {
  return {
    schedule: {
      runOn: ["sessionStart", "plateau2", "finalAudit"]
    },
    dimensions: [],
    sources: []
  };
}

function loadScorecard(root) {
  return readJsonSubsetYaml(path.join(root, ".github", "agent-data", "scorecard.yml"), defaultScorecard());
}

function loadBenchmarkConfig(root) {
  return readJsonSubsetYaml(
    path.join(root, ".github", "agent-data", "benchmark-sources.yml"),
    defaultBenchmarkConfig()
  );
}

function nowIso() {
  return new Date().toISOString();
}

function uniq(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function normalizeStatus(status) {
  const normalized = String(status || "unknown").toLowerCase();
  if (["pass", "fail", "unknown", "stale"].includes(normalized)) {
    return normalized;
  }

  return "unknown";
}

function statusPass(status) {
  return normalizeStatus(status) === "pass";
}

function normalizeSeverity(severity) {
  const normalized = String(severity || "info").toLowerCase();
  if (["critical", "high", "medium", "low", "info"].includes(normalized)) {
    return normalized;
  }

  return "info";
}

function normalizeFindings(items) {
  if (!Array.isArray(items)) {
    return [];
  }

  return items.map((item) => ({
    ...item,
    severity: normalizeSeverity(item.severity)
  }));
}

function blockingFindings(items, blockerSeverities) {
  const allowed = new Set((blockerSeverities || []).map(normalizeSeverity));
  return normalizeFindings(items).filter((item) => allowed.has(item.severity));
}

function summarize(value, maxLength = 240) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function ensureStateShape(state, scorecard, benchmarkConfig) {
  const safeState = state || {};

  safeState.iteration = Number.isFinite(safeState.iteration) ? safeState.iteration : 0;
  safeState.score = Number.isFinite(safeState.score) ? safeState.score : 0;
  safeState.previousScore = Number.isFinite(safeState.previousScore) ? safeState.previousScore : null;
  safeState.forcedContinuations = Number.isFinite(safeState.forcedContinuations)
    ? safeState.forcedContinuations
    : 0;
  safeState.gaps = Array.isArray(safeState.gaps) ? safeState.gaps : [];
  safeState.blockers = Array.isArray(safeState.blockers) ? safeState.blockers : [];
  safeState.nextDirective = typeof safeState.nextDirective === "string" ? safeState.nextDirective : "";
  safeState.lastBenchmarkAt = typeof safeState.lastBenchmarkAt === "string" ? safeState.lastBenchmarkAt : null;
  safeState.status = typeof safeState.status === "string" ? safeState.status : "running";
  safeState.scorecard = safeState.scorecard || {
    targetScore: scorecard.targetScore,
    blockerSeverities: scorecard.blockerSeverities,
    maxForcedContinuations: scorecard.maxForcedContinuations
  };
  safeState.benchmarkSchedule = safeState.benchmarkSchedule || benchmarkConfig.schedule;
  safeState.checks = safeState.checks || {};

  for (const key of ["tests", "lint", "benchmark"]) {
    safeState.checks[key] = safeState.checks[key] || {};
    safeState.checks[key].status = normalizeStatus(safeState.checks[key].status);
    safeState.checks[key].evidence = typeof safeState.checks[key].evidence === "string"
      ? safeState.checks[key].evidence
      : "";
    safeState.checks[key].lastCommand = typeof safeState.checks[key].lastCommand === "string"
      ? safeState.checks[key].lastCommand
      : "";
    safeState.checks[key].updatedAt = typeof safeState.checks[key].updatedAt === "string"
      ? safeState.checks[key].updatedAt
      : null;
  }

  safeState.meta = safeState.meta || {};
  safeState.meta.plateauCount = Number.isFinite(safeState.meta.plateauCount) ? safeState.meta.plateauCount : 0;
  safeState.meta.filesTouched = Array.isArray(safeState.meta.filesTouched) ? safeState.meta.filesTouched : [];
  safeState.meta.lastCommands = Array.isArray(safeState.meta.lastCommands) ? safeState.meta.lastCommands : [];
  safeState.meta.lastUpdatedAt = typeof safeState.meta.lastUpdatedAt === "string"
    ? safeState.meta.lastUpdatedAt
    : nowIso();

  return safeState;
}

function createInitialState(input, scorecard, benchmarkConfig) {
  return ensureStateShape(
    {
      sessionId: sanitizeSessionId(input.sessionId),
      createdAt: nowIso(),
      updatedAt: nowIso(),
      scorecard: {
        targetScore: scorecard.targetScore,
        blockerSeverities: scorecard.blockerSeverities,
        maxForcedContinuations: scorecard.maxForcedContinuations
      },
      benchmarkSchedule: benchmarkConfig.schedule
    },
    scorecard,
    benchmarkConfig
  );
}

function loadState(root, input, scorecard, benchmarkConfig) {
  const filePath = stateFilePath(root, input.sessionId);
  if (!fs.existsSync(filePath)) {
    return createInitialState(input, scorecard, benchmarkConfig);
  }

  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  return ensureStateShape(parsed, scorecard, benchmarkConfig);
}

function saveState(root, input, state) {
  ensureDir(stateDir(root));
  state.updatedAt = nowIso();
  state.meta = state.meta || {};
  state.meta.lastUpdatedAt = state.updatedAt;
  fs.writeFileSync(stateFilePath(root, input.sessionId), JSON.stringify(state, null, 2));
}

function collectStrings(value, bucket = []) {
  if (typeof value === "string") {
    bucket.push(value);
    return bucket;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      collectStrings(item, bucket);
    }
    return bucket;
  }

  if (value && typeof value === "object") {
    for (const nestedValue of Object.values(value)) {
      collectStrings(nestedValue, bucket);
    }
  }

  return bucket;
}

function readTranscriptText(input) {
  const transcriptPath = input.transcript_path;
  if (!transcriptPath || !fs.existsSync(transcriptPath)) {
    return "";
  }

  const raw = fs.readFileSync(transcriptPath, "utf8");

  try {
    const parsed = JSON.parse(raw);
    return collectStrings(parsed).join("\n");
  } catch {
    return raw;
  }
}

function extractTaggedJson(text, tagName) {
  if (!text) {
    return null;
  }

  const pattern = new RegExp(`<${tagName}>\\s*([\\s\\S]*?)\\s*<\\/${tagName}>`, "g");
  let lastMatch = null;

  for (const match of text.matchAll(pattern)) {
    lastMatch = match[1];
  }

  if (!lastMatch) {
    return null;
  }

  try {
    return JSON.parse(lastMatch);
  } catch {
    return null;
  }
}

function extractChangedFiles(toolInput) {
  if (!toolInput || typeof toolInput !== "object") {
    return [];
  }

  const found = [];

  for (const key of ["files", "paths"]) {
    if (Array.isArray(toolInput[key])) {
      found.push(...toolInput[key].filter((value) => typeof value === "string"));
    }
  }

  for (const key of ["path", "file", "filePath", "targetPath"]) {
    if (typeof toolInput[key] === "string") {
      found.push(toolInput[key]);
    }
  }

  return uniq(found);
}

function extractCommand(toolInput) {
  if (!toolInput || typeof toolInput !== "object") {
    return "";
  }

  for (const key of ["command", "shellCommand", "text"]) {
    if (typeof toolInput[key] === "string") {
      return toolInput[key];
    }
  }

  if (Array.isArray(toolInput.commands)) {
    return toolInput.commands.join(" && ");
  }

  return "";
}

function toolResponseText(toolResponse) {
  if (typeof toolResponse === "string") {
    return toolResponse;
  }

  if (toolResponse == null) {
    return "";
  }

  try {
    return JSON.stringify(toolResponse);
  } catch {
    return String(toolResponse);
  }
}

function detectCommandCategory(command) {
  const text = String(command || "").toLowerCase();
  if (!text) {
    return "other";
  }

  if (/\b(test|pytest|vitest|jest|mocha|ava|go test|cargo test|dotnet test)\b/.test(text)) {
    return "tests";
  }

  if (/\b(lint|eslint|ruff|flake8|checkstyle|golangci-lint|cargo clippy)\b/.test(text)) {
    return "lint";
  }

  if (/\b(benchmark|compare|competitive|competitor|audit-web|market)\b/.test(text)) {
    return "benchmark";
  }

  if (/\b(deploy|kubectl|terraform|helm|flyctl|vercel|netlify)\b/.test(text)) {
    return "deploy";
  }

  if (/\b(publish|release|npm publish|pnpm publish|yarn publish|gh release)\b/.test(text)) {
    return "publish";
  }

  return "other";
}

function detectCommandStatus(command, toolResponse) {
  const text = `${command}\n${toolResponseText(toolResponse)}`.toLowerCase();

  if (/("exitCode":\s*[1-9]\d*)|\bexit code[:= ]+[1-9]\d*\b|\bfailed\b|\berror\b|\bnot ok\b|\b0 passing\b/.test(text)) {
    return "fail";
  }

  if (/("exitCode":\s*0)|\bpassed\b|\bok\b|\b0 failing\b|\bsuccess\b/.test(text)) {
    return "pass";
  }

  return "unknown";
}

module.exports = {
  blockingFindings,
  createInitialState,
  defaultBenchmarkConfig,
  defaultScorecard,
  detectCommandCategory,
  detectCommandStatus,
  ensureDir,
  ensureStateShape,
  extractChangedFiles,
  extractCommand,
  extractTaggedJson,
  loadBenchmarkConfig,
  loadScorecard,
  loadState,
  normalizeFindings,
  normalizeSeverity,
  normalizeStatus,
  nowIso,
  readHookInput,
  readTranscriptText,
  repoRoot,
  sanitizeSessionId,
  saveState,
  stateFilePath,
  statusPass,
  summarize,
  toolResponseText,
  uniq,
  writeJson
};
