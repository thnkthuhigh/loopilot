const {
  detectCommandCategory,
  extractCommand,
  readHookInput,
  writeJson
} = require("./shared.cjs");

const DANGEROUS_COMMAND = /(?:git\s+reset\s+--hard|git\s+clean\s+-fdx?|rm\s+-rf\b|del\s+\/[sqf]|remove-item\b[^\n\r]*-recurse[^\n\r]*-force|npm\s+publish\b|pnpm\s+publish\b|yarn\s+publish\b|gh\s+release\s+create\b|kubectl\b[^\n\r]*\b(apply|delete|rollout|scale)\b|terraform\b[^\n\r]*\b(apply|destroy)\b)/i;
const MERGE_OR_DEPLOY_TOOL = /^(pushToGitHub|createPullRequest|mergePullRequest|deployApplication)$/i;

async function main() {
  const input = await readHookInput();
  const toolName = String(input.tool_name || "");
  const command = extractCommand(input.tool_input);
  const category = detectCommandCategory(command);

  if (MERGE_OR_DEPLOY_TOOL.test(toolName)) {
    writeJson({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Remote publish, merge, and deploy actions are disabled by the local supervisor policy."
      }
    });
    return;
  }

  if (command && DANGEROUS_COMMAND.test(command)) {
    writeJson({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Destructive or release-oriented terminal command blocked by supervisor policy.",
        additionalContext: `Blocked command category: ${category || "dangerous"}. Use a safer local workflow instead.`
      }
    });
    return;
  }

  if (category === "deploy" || category === "publish") {
    writeJson({
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Deploy and publish commands are not allowed in this workspace."
      }
    });
    return;
  }

  writeJson({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "allow"
    }
  });
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(2);
});
