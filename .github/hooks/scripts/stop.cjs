const {
  blockingFindings,
  extractTaggedJson,
  loadBenchmarkConfig,
  loadScorecard,
  loadState,
  normalizeFindings,
  normalizeStatus,
  nowIso,
  readHookInput,
  readTranscriptText,
  repoRoot,
  saveState,
  statusPass,
  writeJson
} = require("./shared.cjs");

function applyAuditToState(state, audit) {
  if (!audit || typeof audit !== "object") {
    return { hasAudit: false, improved: false };
  }

  const nextScore = Number.isFinite(Number(audit.score)) ? Number(audit.score) : state.score;
  const previousScore = Number.isFinite(state.score) ? state.score : 0;
  const improved = nextScore > previousScore;

  state.previousScore = previousScore;
  state.score = nextScore;
  state.gaps = normalizeFindings(audit.gaps);
  state.blockers = normalizeFindings(audit.blockers);
  state.nextDirective = typeof audit.nextDirective === "string" ? audit.nextDirective.trim() : "";
  state.summary = typeof audit.summary === "string" ? audit.summary.trim() : "";

  for (const key of ["tests", "lint", "benchmark"]) {
    const check = audit.checks && audit.checks[key] ? audit.checks[key] : {};
    if (check.status) {
      state.checks[key].status = normalizeStatus(check.status);
    }
    if (typeof check.evidence === "string") {
      state.checks[key].evidence = check.evidence.trim();
    }
    state.checks[key].updatedAt = nowIso();
  }

  if (state.checks.benchmark.status === "pass" || state.checks.benchmark.status === "fail") {
    state.lastBenchmarkAt = nowIso();
  }

  state.meta.plateauCount = improved ? 0 : (state.meta.plateauCount || 0) + 1;
  return { hasAudit: true, improved };
}

function buildNextDirective(audit, state, blockingBlockers, scorecard) {
  if (audit && typeof audit.nextDirective === "string" && audit.nextDirective.trim()) {
    return audit.nextDirective.trim();
  }

  if (blockingBlockers.length > 0) {
    const blocker = blockingBlockers[0];
    return `Resolve the ${blocker.severity} blocker "${blocker.title}" and rerun the Auditor.`;
  }

  if (!statusPass(state.checks.tests.status)) {
    return "Run the relevant test suite, fix the failures, and rerun the Auditor.";
  }

  if (!statusPass(state.checks.lint.status)) {
    return "Run the relevant lint or static checks, fix the failures, and rerun the Auditor.";
  }

  if ((state.score || 0) < scorecard.targetScore) {
    return `The current score is ${state.score}/${scorecard.targetScore}. Improve the highest-value gap and rerun the Auditor.`;
  }

  return "Rerun the Auditor with a clear stop-readiness decision.";
}

async function main() {
  const input = await readHookInput();
  const root = repoRoot(input);
  const scorecard = loadScorecard(root);
  const benchmarkConfig = loadBenchmarkConfig(root);
  const state = loadState(root, input, scorecard, benchmarkConfig);
  const transcriptText = readTranscriptText(input);
  const audit = extractTaggedJson(transcriptText, "SUPERVISOR_AUDIT");

  state.iteration += 1;
  const { hasAudit, improved } = applyAuditToState(state, audit);
  const blockingBlockers = blockingFindings(state.blockers, scorecard.blockerSeverities);
  const meetsScore = (state.score || 0) >= scorecard.targetScore;
  const testsPass = statusPass(state.checks.tests.status);
  const lintPass = statusPass(state.checks.lint.status);
  const readyFlag = audit && audit.readyToStop === true;
  const stopReady = hasAudit && (readyFlag || (meetsScore && testsPass && lintPass && blockingBlockers.length === 0));

  state.status = "running";

  if (stopReady) {
    state.status = "complete";
    state.nextDirective = "";
    saveState(root, input, state);
    writeJson({
      continue: true,
      systemMessage: `Supervisor complete at ${state.score}/${scorecard.targetScore}. Tests and lint are passing and no blocking findings remain.`
    });
    return;
  }

  const noProgress = state.meta.plateauCount >= 2;
  const maxedOut = state.forcedContinuations >= scorecard.maxForcedContinuations;
  const directive = hasAudit
    ? buildNextDirective(audit, state, blockingBlockers, scorecard)
    : "Run the Auditor subagent now and emit a <SUPERVISOR_AUDIT>{...}</SUPERVISOR_AUDIT> report before finishing.";

  if (!maxedOut && (!input.stop_hook_active || improved) && !noProgress) {
    state.forcedContinuations += 1;
    state.status = "continuing";
    state.nextDirective = directive;
    saveState(root, input, state);
    writeJson({
      hookSpecificOutput: {
        hookEventName: "Stop",
        decision: "block",
        reason: directive
      },
      systemMessage: `Supervisor blocked stop at ${state.score}/${scorecard.targetScore}. Forced continuation ${state.forcedContinuations}/${scorecard.maxForcedContinuations}.`
    });
    return;
  }

  state.status = "blocked";
  state.nextDirective = directive;
  saveState(root, input, state);
  writeJson({
    continue: true,
    systemMessage:
      `Supervisor is allowing the session to stop as blocked. ` +
      `Score: ${state.score}/${scorecard.targetScore}. ` +
      `Forced continuations: ${state.forcedContinuations}/${scorecard.maxForcedContinuations}. ` +
      `Next directive: ${directive}`
  });
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(2);
});
