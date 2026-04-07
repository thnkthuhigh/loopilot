const fs = require("node:fs");
const path = require("node:path");

function main() {
  const root = process.cwd();
  const stateRoot = path.join(root, ".copilot", "supervisor");
  if (!fs.existsSync(stateRoot)) {
    console.log("No supervisor state directory found.");
    return;
  }

  const requestedSession = process.argv[2];
  let targetFile = requestedSession ? path.join(stateRoot, `${requestedSession}.json`) : null;

  if (!targetFile) {
    const files = fs
      .readdirSync(stateRoot)
      .filter((fileName) => fileName.endsWith(".json"))
      .map((fileName) => ({
        fileName,
        modifiedAt: fs.statSync(path.join(stateRoot, fileName)).mtimeMs
      }))
      .sort((left, right) => right.modifiedAt - left.modifiedAt);

    if (files.length === 0) {
      console.log("No supervisor state files found.");
      return;
    }

    targetFile = path.join(stateRoot, files[0].fileName);
  }

  if (!fs.existsSync(targetFile)) {
    console.error(`State file not found: ${targetFile}`);
    process.exit(1);
  }

  process.stdout.write(`${fs.readFileSync(targetFile, "utf8")}\n`);
}

main();
