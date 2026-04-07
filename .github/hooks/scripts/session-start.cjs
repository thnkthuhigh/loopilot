const path = require("node:path");
const {
  loadBenchmarkConfig,
  loadScorecard,
  loadState,
  repoRoot,
  saveState,
  stateFilePath,
  summarize,
  writeJson,
  readHookInput
} = require("./shared.cjs");

async function main() {
  const input = await readHookInput();
  const root = repoRoot(input);
  const scorecard = loadScorecard(root);
  const benchmarkConfig = loadBenchmarkConfig(root);
  const state = loadState(root, input, scorecard, benchmarkConfig);

  state.status = "running";
  saveState(root, input, state);

  const relativeStatePath = path.relative(root, stateFilePath(root, input.sessionId));
  const benchmarkSchedule = summarize((benchmarkConfig.schedule?.runOn || []).join(", "));

  writeJson({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext:
        `Supervisor state file: ${relativeStatePath}. ` +
        `Target score: ${scorecard.targetScore}. ` +
        `Max forced continuations: ${scorecard.maxForcedContinuations}. ` +
        `Stop only when score >= target, tests pass, lint passes, and no ${scorecard.blockerSeverities.join("/")} blockers remain. ` +
        `Benchmark schedule: ${benchmarkSchedule}. ` +
        `Use the exact machine-readable tags from .github/agent-data/runtime-contract.md.`
    }
  });
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(2);
});
