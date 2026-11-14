// The suite's fixture: one temporary directory per run that holds everything the plan window reads
// or writes, so the run never touches ~/.cache/plan, the user's VS Code settings or their
// LaunchAgents. wdio.conf.js creates it and points the test VS Code at it; the specs reach it
// through `fixture` below, by the PLAN_E2E_ROOT the VS Code, the fake plan and the fake Claude
// all inherit.
//
//   <root>/home            HOME of the test VS Code and its terminals
//   <root>/vscode          user data dir and extensions dir of the test VS Code
//   <root>/claude-config   CLAUDE_CONFIG_DIR, where worktreeHasConversation looks
//   <root>/issues, pulses  plan.issuesFile and plan.pulsesFile
//   <root>/records         plan.workspaceDir: plan.code-workspace, the plan window's workspace, and
//                          one <KEY>.code-workspace per issue
//   <root>/other           other.code-workspace, the workspace of a window that is not the plan window
//   <root>/tickets         a store issue's ticket file
//   <root>/worktrees       one directory per issue
//   <root>/plan.log        one line per call of the fake plan
//   <root>/claude.log      one line per start of the fake Claude
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT_VARIABLE = "PLAN_E2E_ROOT";
const FAKES_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "fakes");

// The variables the test VS Code is launched with. The two CLAUDE_* session variables stand for a
// window opened by `code -n` from inside a Claude session; the terminals must not pass them on.
const INHERITED_SESSION_VARIABLES = { CLAUDE_CODE_CHILD_SESSION: "1", CLAUDE_PID: "4242" };

// Under /tmp rather than os.tmpdir(), which on macOS is long enough to push VS Code's socket in the
// user data dir past the 103 characters a unix socket path may have.
function createRoot() {
  return fs.realpathSync(fs.mkdtempSync(path.join("/tmp", "plan-e2e-")));
}

function pathsUnder(root) {
  return {
    root,
    home: path.join(root, "home"),
    storage: path.join(root, "vscode"),
    claudeConfig: path.join(root, "claude-config"),
    issuesFile: path.join(root, "issues"),
    pulsesFile: path.join(root, "pulses"),
    workspaceDir: path.join(root, "records"),
    planWorkspaceFile: path.join(root, "records", "plan.code-workspace"),
    otherDir: path.join(root, "other"),
    otherWorkspaceFile: path.join(root, "other", "other.code-workspace"),
    ticketsDir: path.join(root, "tickets"),
    worktreesDir: path.join(root, "worktrees"),
    planLog: path.join(root, "plan.log"),
    claudeLog: path.join(root, "claude.log"),
    planFailsMarker: path.join(root, "plan-fails"),
    fakePlan: path.join(FAKES_DIR, "plan"),
  };
}

// A login shell reads $HOME/.bash_profile after /etc/profile, whose path_helper reorders PATH; this
// one puts the fake Claude first again, so `claude` in a terminal is the fake whatever else is
// installed.
function writeShellProfile(paths) {
  fs.writeFileSync(path.join(paths.home, ".bash_profile"), `export PATH="${FAKES_DIR}:$PATH"\n`);
}

function createDirectories(paths) {
  for (const dir of [
    paths.home,
    paths.storage,
    paths.claudeConfig,
    paths.workspaceDir,
    paths.otherDir,
    paths.ticketsDir,
    paths.worktreesDir,
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }
  writeShellProfile(paths);
  writeWorkspaces(paths);
}

// The plan window is the window on <workspaceDir>/plan.code-workspace; any other workspace is a
// window the extension must leave alone. Neither holds a folder: the specs need no files open.
function writeWorkspaces(paths) {
  const planSettings = { "plan.workspaceDir": paths.workspaceDir, "plan.command": paths.fakePlan };
  fs.writeFileSync(paths.planWorkspaceFile, JSON.stringify({ folders: [], settings: planSettings }));
  fs.writeFileSync(paths.otherWorkspaceFile, JSON.stringify({ folders: [] }));
}

// What the run's environment adds to the launcher's: HOME and SHELL fixed so no user profile is
// read and the terminals' shell is bash, the CLAUDE_* variables the specs expect, and the root.
function runEnvironment(paths) {
  return {
    ...INHERITED_SESSION_VARIABLES,
    HOME: paths.home,
    SHELL: "/bin/bash",
    CLAUDE_CONFIG_DIR: paths.claudeConfig,
    [ROOT_VARIABLE]: paths.root,
  };
}

function clearClaudeVariables() {
  for (const name of Object.keys(process.env)) {
    if (name.startsWith("CLAUDE")) delete process.env[name];
  }
}

// The plan.* settings that point the extension at the fixture, as VS Code user settings.
export function pluginSettings(paths) {
  return {
    "plan.issuesFile": paths.issuesFile,
    "plan.pulsesFile": paths.pulsesFile,
    "plan.workspaceDir": paths.workspaceDir,
    "plan.command": paths.fakePlan,
  };
}

// Makes the run's fixture directory and applies its environment to this process, which the test
// VS Code is a descendant of; the wdio workers inherit it. Called once, by the wdio launcher.
export function createRunFixture() {
  const paths = pathsUnder(createRoot());
  createDirectories(paths);
  clearClaudeVariables();
  Object.assign(process.env, runEnvironment(paths));
  return paths;
}

export function removeRunFixture(paths) {
  fs.rmSync(paths.root, { recursive: true, force: true });
}

// What the specs do to the fixture's files; the paths come from the run's root.
class Fixture {
  constructor(root) {
    this.paths = pathsUnder(root);
  }

  // Back to no issues, no pulses, no logs and a succeeding plan, between specs.
  reset() {
    for (const file of [
      this.paths.issuesFile,
      this.paths.pulsesFile,
      this.paths.planLog,
      this.paths.claudeLog,
      this.paths.planFailsMarker,
    ]) {
      fs.rmSync(file, { force: true });
    }
    fs.rmSync(this.paths.claudeConfig, { recursive: true, force: true });
    fs.mkdirSync(this.paths.claudeConfig);
  }

  writeIssues(keys) {
    fs.writeFileSync(this.paths.issuesFile, keys.map((key) => `${key}\n`).join(""));
  }

  writePulses(colorsByKey) {
    const lines = Object.entries(colorsByKey).map(([key, color]) => `${key}=${color}\n`);
    fs.writeFileSync(this.paths.pulsesFile, lines.join(""));
  }

  // A store issue: its ticket file, worktree and record. Returns where they are.
  addStoreIssue(key) {
    const ticket = path.join(this.paths.ticketsDir, `${key}-example.md`);
    fs.writeFileSync(ticket, `# ${key}\n\nA fixture ticket.\n`);
    const worktree = this.addWorktree(key);
    this.writeRecord(key, { "plan.ticket": ticket }, worktree);
    return { key, ticket, worktree };
  }

  // A Jira issue: no ticket file, a browse URL instead.
  addJiraIssue(key, ticketUrl) {
    const worktree = this.addWorktree(key);
    this.writeRecord(key, { "plan.ticketUrl": ticketUrl }, worktree);
    return { key, ticketUrl, worktree };
  }

  addWorktree(key) {
    const worktree = path.join(this.paths.worktreesDir, key);
    fs.mkdirSync(worktree, { recursive: true });
    return worktree;
  }

  writeRecord(key, ticketSettings, worktree) {
    const settings = {
      "plan.key": key,
      "plan.worktree": worktree,
      "plan.prompt": `Work on ${key}.`,
      ...ticketSettings,
    };
    fs.writeFileSync(
      path.join(this.paths.workspaceDir, `${key}.code-workspace`),
      JSON.stringify({ folders: [], settings }),
    );
  }

  // A conversation Claude Code left in the worktree: <config dir>/projects/<worktree, every
  // non-alphanumeric character as "-">/<session>.jsonl.
  // https://code.claude.com/docs/en/sessions#where-transcripts-are-stored
  addConversation(worktree) {
    const dir = path.join(this.paths.claudeConfig, "projects", worktree.replace(/[^A-Za-z0-9]/g, "-"));
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "00000000-0000-0000-0000-000000000000.jsonl"), "{}\n");
  }

  makePlanFail() {
    fs.writeFileSync(this.paths.planFailsMarker, "");
  }

  // The fake plan's calls, one string of arguments each, as "pulse SK-0101 off".
  planCalls() {
    return readLines(this.paths.planLog);
  }

  // The fake Claude's starts: its issue, its arguments each in brackets ("[--continue]"), its
  // CLAUDE_CONFIG_DIR, and the names of the other CLAUDE* variables it had, or "none".
  claudeStarts() {
    return readLines(this.paths.claudeLog).map(parseClaudeStart);
  }
}

function parseClaudeStart(line) {
  const field = (name) =>
    line
      .split("\t")
      .find((part) => part.startsWith(`${name}=`))
      .slice(name.length + 1);
  return {
    key: field("key"),
    args: field("args"),
    configDir: field("config_dir"),
    otherClaudeVariables: field("other_claude_variables"),
  };
}

function readLines(file) {
  try {
    return fs.readFileSync(file, "utf8").split("\n").filter(Boolean);
  } catch {
    return [];
  }
}

export function openFixture() {
  const root = process.env[ROOT_VARIABLE];
  if (!root) throw new Error(`${ROOT_VARIABLE} is unset: the specs run under wdio.conf.js`);
  return new Fixture(root);
}
