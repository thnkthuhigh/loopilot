const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "..");
const scriptsRoot = path.join(repoRoot, ".github", "hooks", "scripts");
const fixturesRoot = path.join(repoRoot, "tests", "fixtures");
const stateRoot = path.join(repoRoot, ".copilot", "supervisor");

function runHook(scriptName, input) {
  const result = spawnSync(process.execPath, [path.join(scriptsRoot, scriptName)], {
    cwd: repoRoot,
    input: JSON.stringify(input),
    encoding: "utf8"
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result.stdout.trim() ? JSON.parse(result.stdout) : {};
}

function stateFile(sessionId) {
  return path.join(stateRoot, `${sessionId}.json`);
}

function cleanup(sessionId) {
  const filePath = stateFile(sessionId);
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
  }
}

function readState(sessionId) {
  return JSON.parse(fs.readFileSync(stateFile(sessionId), "utf8"));
}

test("SessionStart creates state and injects setup context", () => {
  const sessionId = "test-session-start";
  cleanup(sessionId);

  const output = runHook("session-start.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "SessionStart"
  });

  assert.equal(typeof output.hookSpecificOutput.additionalContext, "string");
  assert.match(output.hookSpecificOutput.additionalContext, /Target score: 85/);
  assert.equal(fs.existsSync(stateFile(sessionId)), true);

  cleanup(sessionId);
});

test("PreToolUse denies destructive terminal commands", () => {
  const output = runHook("pre-tool-use.cjs", {
    cwd: repoRoot,
    sessionId: "test-pretool",
    hookEventName: "PreToolUse",
    tool_name: "runTerminalCommand",
    tool_input: {
      command: "git reset --hard && git clean -fdx"
    }
  });

  assert.equal(output.hookSpecificOutput.permissionDecision, "deny");
});

test("Stop blocks low-score sessions and persists the next directive", () => {
  const sessionId = "test-stop-block";
  cleanup(sessionId);
  runHook("session-start.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "SessionStart"
  });

  const output = runHook("stop.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "Stop",
    stop_hook_active: false,
    transcript_path: path.join(fixturesRoot, "transcript-low-score.json")
  });

  assert.equal(output.hookSpecificOutput.hookEventName, "Stop");
  assert.equal(output.hookSpecificOutput.decision, "block");

  const state = readState(sessionId);
  assert.equal(state.status, "continuing");
  assert.equal(state.score, 62);
  assert.equal(state.forcedContinuations, 1);
  assert.match(state.nextDirective, /Fix the failing tests/);

  cleanup(sessionId);
});

test("Stop allows ready sessions to finish", () => {
  const sessionId = "test-stop-ready";
  cleanup(sessionId);
  runHook("session-start.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "SessionStart"
  });

  const output = runHook("stop.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "Stop",
    stop_hook_active: false,
    transcript_path: path.join(fixturesRoot, "transcript-ready.json")
  });

  assert.equal(output.continue, true);
  assert.equal(output.hookSpecificOutput, undefined);

  const state = readState(sessionId);
  assert.equal(state.status, "complete");
  assert.equal(state.score, 91);

  cleanup(sessionId);
});

test("Stop marks the session as blocked after plateaued no-progress retries", () => {
  const sessionId = "test-stop-plateau";
  cleanup(sessionId);
  runHook("session-start.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "SessionStart"
  });

  const filePath = stateFile(sessionId);
  const seeded = readState(sessionId);
  seeded.score = 62;
  seeded.previousScore = 62;
  seeded.forcedContinuations = 2;
  seeded.meta.plateauCount = 1;
  fs.writeFileSync(filePath, JSON.stringify(seeded, null, 2));

  const output = runHook("stop.cjs", {
    cwd: repoRoot,
    sessionId,
    hookEventName: "Stop",
    stop_hook_active: true,
    transcript_path: path.join(fixturesRoot, "transcript-low-score.json")
  });

  assert.equal(output.continue, true);
  assert.match(output.systemMessage, /allowing the session to stop as blocked/i);

  const state = readState(sessionId);
  assert.equal(state.status, "blocked");

  cleanup(sessionId);
});
