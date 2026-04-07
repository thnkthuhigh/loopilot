const {
  detectCommandCategory,
  detectCommandStatus,
  extractChangedFiles,
  extractCommand,
  loadBenchmarkConfig,
  loadScorecard,
  loadState,
  nowIso,
  readHookInput,
  repoRoot,
  saveState,
  summarize,
  toolResponseText,
  uniq,
  writeJson
} = require("./shared.cjs");

function pushCommandHistory(state, command) {
  if (!command) {
    return;
  }

  const entry = summarize(command, 180);
  state.meta.lastCommands = uniq([entry, ...(state.meta.lastCommands || [])]).slice(0, 10);
}

async function main() {
  const input = await readHookInput();
  const root = repoRoot(input);
  const scorecard = loadScorecard(root);
  const benchmarkConfig = loadBenchmarkConfig(root);
  const state = loadState(root, input, scorecard, benchmarkConfig);

  const toolName = String(input.tool_name || "");
  const changedFiles = extractChangedFiles(input.tool_input);
  const command = extractCommand(input.tool_input);
  const category = detectCommandCategory(command);
  const status = detectCommandStatus(command, input.tool_response);
  const responseText = summarize(toolResponseText(input.tool_response), 280);

  if (changedFiles.length > 0) {
    state.meta.filesTouched = uniq([...(state.meta.filesTouched || []), ...changedFiles]).slice(-100);
  }

  pushCommandHistory(state, command);

  if (["tests", "lint", "benchmark"].includes(category)) {
    state.checks[category].status = status;
    state.checks[category].lastCommand = summarize(command, 180);
    state.checks[category].evidence = responseText;
    state.checks[category].updatedAt = nowIso();
  }

  if (category === "benchmark" || /^(fetchWebPage|webSearch|openExternalUrl)$/i.test(toolName)) {
    state.lastBenchmarkAt = nowIso();
    if (category === "benchmark" && state.checks.benchmark.status === "unknown") {
      state.checks.benchmark.status = "pass";
      state.checks.benchmark.updatedAt = state.lastBenchmarkAt;
      state.checks.benchmark.evidence = responseText || "Benchmark activity detected.";
    }
  }

  saveState(root, input, state);

  if ((category === "tests" || category === "lint") && status === "fail") {
    writeJson({
      decision: "block",
      reason: `${category} command reported a failure`,
      hookSpecificOutput: {
        hookEventName: "PostToolUse",
        additionalContext:
          `Latest ${category} command failed. Review the output before moving on. ` +
          `${responseText}`
      }
    });
    return;
  }

  writeJson({
    continue: true
  });
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(2);
});
