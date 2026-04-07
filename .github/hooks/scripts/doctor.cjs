const fs = require("node:fs");
const path = require("node:path");

const root = process.cwd();

const requiredFiles = [
  ".github/copilot-instructions.md",
  ".github/agent-data/scorecard.yml",
  ".github/agent-data/benchmark-sources.yml",
  ".github/agent-data/runtime-contract.md",
  ".github/agents/supervisor.agent.md",
  ".github/agents/researcher.agent.md",
  ".github/agents/implementer.agent.md",
  ".github/agents/auditor.agent.md",
  ".github/hooks/supervisor.json",
  ".github/hooks/scripts/session-start.cjs",
  ".github/hooks/scripts/pre-tool-use.cjs",
  ".github/hooks/scripts/post-tool-use.cjs",
  ".github/hooks/scripts/stop.cjs",
  ".vscode/settings.json"
];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function line(ok, text) {
  return `${ok ? "[ok]" : "[warn]"} ${text}`;
}

function main() {
  const lines = [];

  for (const relativePath of requiredFiles) {
    const absolutePath = path.join(root, relativePath);
    lines.push(line(fs.existsSync(absolutePath), `exists: ${relativePath}`));
  }

  const settingsPath = path.join(root, ".vscode", "settings.json");
  if (fs.existsSync(settingsPath)) {
    const settings = readJson(settingsPath);
    lines.push(
      line(
        settings["chat.useCustomAgentHooks"] === true,
        "workspace setting chat.useCustomAgentHooks is enabled"
      )
    );
  }

  lines.push(line(fs.existsSync(path.join(root, ".git")), "workspace is a git repository"));
  lines.push(line(true, "Open VS Code and run Chat: Configure Hooks if you want to inspect the loaded hooks."));
  lines.push(line(true, "Use the Supervisor custom agent for the full research -> implement -> audit loop."));

  process.stdout.write(`${lines.join("\n")}\n`);
}

main();
