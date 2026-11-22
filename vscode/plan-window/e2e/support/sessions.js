// Which of the suite's two sessions a run's --spec arguments call for. Pure: the expansion of a
// pattern into files is passed in, so wdio.conf.js hands it wdio's own (see there) and the test
// hands it a table.
import path from "node:path";

export const OTHER_WINDOW_FOLDER = path.join("specs", "other-window");

// The values of every --spec / --spec=<value> in the launcher's arguments.
export function specArguments(argv) {
  return argv.flatMap((arg, at) => (arg === "--spec" ? [argv[at + 1]] : arg.startsWith("--spec=") ? [arg.slice(7)] : []));
}

function belongsToOtherWindow(file, e2eDir) {
  return path.resolve(file).startsWith(path.join(e2eDir, OTHER_WINDOW_FOLDER) + path.sep);
}

// The files one --spec pattern names, or an error saying it names none.
function filesOf(pattern, expand) {
  const files = expand(pattern);
  if (files.length === 0) throw new Error(`--spec ${pattern} matches no spec file.`);
  return files;
}

// "plan", "other" or "both": the sessions the patterns call for, and both with none given. Throws
// when a pattern matches no file, and when the files belong to both sessions.
export function sessionsFor(patterns, expand, e2eDir) {
  if (patterns.length === 0) return "both";
  const files = patterns.flatMap((pattern) => filesOf(pattern, expand));
  const other = files.filter((file) => belongsToOtherWindow(file, e2eDir)).length;
  if (other === 0) return "plan";
  if (other === files.length) return "other";
  throw new Error("--spec names specs of both sessions (specs/ and specs/other-window/); run one session at a time.");
}
