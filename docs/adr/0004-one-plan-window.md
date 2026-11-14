# ADR 0004: One plan window, with an Issues view, for all issues

<a id="status"></a>

## 1. Status

Accepted. Replaces [ADR 0003](0003-native-desktops.md).

<a id="context"></a>

## 2. Context

[ADR 0003](0003-native-desktops.md) put each issue's VS Code window on its own native macOS Desktop.
Doing that took a `WindowPlacer` port and its `SpacesPlacer` adapter, the `skuld-spaces` Swift
helper with its SkyLight reads, ^N shortcuts sent through System Events, a slots file of
`<slot>=<KEY>` lines, a `windowsFile` of window ids, and a Mission Control setup done by hand. It
also left a list of failure cases: a window on the wrong Desktop, on a taken Desktop, in full
screen, or off the record; no free Desktop; a SkyLight call that a macOS update removes.

All of that machinery answers one question: which Desktop is the issue's window on. The human
does not need `plan` to answer it. What they need is the issue's Claude in front of them, and VS
Code finds a window by itself: `code <workspace file>` opens the workspace, or raises the window
that already shows it, wherever that window is.

SlotBar, a Swift menu-bar app, and the Claude status engine key their state by Desktop number,
through the slots file, so they depend on the same machinery. SlotBar also needs its own build,
signing, LaunchAgent and a notification channel (`slotbar-pulse`) to show a colour per issue, while
the human works in VS Code, where skuld's `plan-window` extension already runs.

Each issue's Claude runs in an integrated terminal of a VS Code window. With a window per issue,
the human goes to each issue's window to reach its Claude, and a list of issues in one window has
no way to bring another issue's Claude to where the human is working.

<a id="decision"></a>

## 3. Decision

There is one VS Code window for all issues, the plan window, so every issue's Claude is reached
from one Issues view in one window. Start and Open run `code <plan workspace>` to open or raise
it, and each issue's Claude runs in a terminal of that window. `plan` reads, switches and assigns no Desktop.

- **VS Code places the window.** `code <plan workspace>` opens the plan workspace, or raises its
  window where it is. No Desktop switch, ^N shortcut or SkyLight read is involved.
- **A plan workspace file lists every issue's worktree and the ticket store.**
  `<workspaceDir>/plan.code-workspace` lists, as folders, the ticket store's folder and the
  worktrees of the issues in the issues file. `plan` writes it through a temp file and a rename. The
  store's folder is always first: Start and Open add it, or move it to the front, so Done never
  removes the folder VS Code names the workspace by. Start and Open add the issue's worktree, and
  Done removes it; Done never removes the store folder. The workspace's settings carry `plan.command`,
  the absolute path of the running checkout's `bin/plan`, which overrides the extension's default.
- **Each issue keeps a workspace file of its own as its record.** Start and Open write
  `<workspaceDir>/<KEY>.code-workspace` with the issue's `plan.key`, `plan.worktree` and
  `plan.prompt`, and either `plan.ticket`, a store ticket's file, or `plan.ticketUrl`, a Jira
  issue's browse URL, set only when there is no `plan.ticket`. It holds settings only: no folders,
  no window title and no terminal environment. `plan-window` reads it to open the issue and sets
  `PLAN_KEY` in the issue's terminals. No window opens on it.
- **The slots file, `windowsFile`, `SpacesPlacer`, `skuld-spaces`, `macos/spaces.swift` and
  `bin/build-spaces` go.** So do the `slotsFile`, `windowsFile`, `spacesHelper` and
  `reservedDesktops` config keys.
- **The `WindowPlacer` port goes.** It hid a window server that tests could not run. Opening the
  window is one `code` call through `run`, the subprocess seam that every Start and Open test
  already fakes, and there is no second adapter in view. A port with one trivial adapter and no
  boundary to protect is indirection, so Start and Open call a `term` function that runs `code`.
- **An issues file replaces the slots file.** It lists the Started or Opened issues that are not
  yet Done, one `<KEY>` per line. Start and Open add a key and Done removes it. It is the roster
  that the plan workspace, the Issues view and the status engine follow.
- **An "Issues" view in VS Code's Explorer replaces SlotBar.** `plan-window` contributes the view
  `plan.issues` under `contributes.views.explorer`. It shows one item per key in the issues file,
  in file order, labelled with the key and tinted from the pulses file. Clicking an item opens that
  issue. The Swift SlotBar, `slotbar-pulse` and SlotBar's build, install, start and test scripts
  go.
- **Opening an issue brings up its Claude.** A click on the issue's item, or the link
  `vscode://skuld.plan-window/open?key=<KEY>`, focuses the window's "Claude <KEY>" terminal when
  there is one. Otherwise it creates that terminal in the issue's worktree, running
  `claude --continue` when the worktree has an earlier Claude session and `claude "$PLAN_PROMPT"`
  when it has none. The order is fixed: first the issue's ticket, every time (a store ticket in an
  editor tab, or a Jira issue's browse URL in the integrated browser; a tab already showing it is
  brought to the front, never a second one), then the shell beside
  Claude, then the Claude terminal, and last the colour is cleared. Claude comes after the ticket
  and the shell so that focus ends on Claude.
- **Start and Open end in the plan window.** Once the worktree, the issue's workspace file, the
  issues file and the plan workspace are written, they run `code <plan workspace>`. If `code`
  fails, Start stops and prints the command to run by hand. They then open the issue's `/open`
  link, best-effort.
- **Only the plan window acts.** `plan-window` loads in every VS Code window but acts only in the
  window whose workspace file is `<plan.workspaceDir>/plan.code-workspace`, through the context key
  `plan.isPlanWindow`. Elsewhere there is no Issues view, no click command and no watcher, and an
  `/open` or `/browse` link shows "this link is for the plan window." and does nothing else.
- **A pulses file carries each issue's colour.** `pulsesFile` (default `~/.cache/plan/pulses`)
  holds `<KEY>=orange` or `<KEY>=green` lines; `off` removes the key's line. Writes go through a
  temp file and a rename. `plan pulse <KEY> <colour>` and the status engine write it, and the Issues view clears a colour
  by running `plan pulse <KEY> off`, only for an issue that has a line. A missing
  file means no colours. The engine keeps what it last sent in `<pulsesFile>.sent`, a JSON object
  `{KEY: colour}` of orange and green, and lays it over the pulses file's colours at start, so a
  colour cleared by a click stays cleared across a restart. The view watches the issues file and the pulses file.
- **Done takes the issue out of the window.** Done removes the key from the issues file, the
  worktree from the plan workspace and the key's line from the pulses file. The issue leaves the
  Issues view. Its `Claude <KEY>` and `Shell <KEY>` terminals stay open until the human closes them,
  because `plan done` is often run in one of them.
- **`plan agents` is keyed by issue.** It shows each Claude's issue key and state, and ticks per
  issue. A session in the worktree of an unlisted key shows as `- (KEY)`.
- **`plan list` and the picker lose the slot column.** With no Desktop there is nothing to show
  beside the status.

<a id="alternatives"></a>

## 4. Alternatives considered

- **Keep native Desktops as ADR 0003 has them.** They keep each issue on a Desktop of its own, at
  the price of a helper, a private-API read, a hand-made Mission Control setup and a long list of
  placement failures. The placement it buys is not needed to reach a window.
- **A window per issue, raised with `code <workspace file>`.** VS Code finds each issue's window by
  its workspace file, so no placement code is needed. But the human goes to each issue's window to
  reach its Claude, and the Issues view cannot bring a Claude into the window where the human is
  working. One window holds every issue's terminal, and moving between issues is a click in the
  Issues view.
- **Keep the `WindowPlacer` port with one adapter that runs `code`.** It satisfies the port rule
  where a fake window server is needed. None is: the one call is `code`, and tests fake `run`. The
  port would carry `sweep`, `slotLabels` and `Placement` for a placement that no longer exists.
- **Keep the slots file with a key and no Desktop number.** It is the issues file under an old
  name; naming it for what it holds avoids a "slot" that is not a slot.
- **Keep SlotBar as a macOS menu-bar app, keyed by issue.** It shows the issues outside VS Code,
  but keeps a second app with its own Swift build, code signing, LaunchAgent and notification
  channel, to show what the human looks at inside VS Code anyway. A view in `plan-window`, which
  the plan window already runs, needs none of these.
- **A separate extension, or a view container of its own, outside the Explorer.** A second
  extension needs its own install and trust, for a view that reads the same files `plan-window`
  is configured for. A view container adds an Activity Bar icon and a sidebar switch to reach the
  list; in the Explorer it sits beside the issues' folders.

<a id="consequences"></a>

## 5. Consequences

- **What goes.** `SpacesPlacer`, `lib/spaces.py`, `lib/placer.py`, `macos/spaces.swift`,
  `bin/build-spaces`, Desktop numbering and ^N switching; SlotBar (`macos/slotbar/`),
  `slotbar-pulse`, `bin/build-slotbar`, `bin/install-slotbar`, `bin/start-slotbar`,
  `bin/test-slotbar` and SlotBar's LaunchAgent. `bin/install-agents` stays, for the status engine.
  An old config that sets a removed key still loads; the key is ignored.
- **Desktops need no setup.** Mission Control settings, ^N shortcuts and the Accessibility grant
  are not required.
- **The plan window opens where VS Code and macOS put it.** Issues have no Desktop each, and
  `plan` does not report a misplaced window.
- **One window holds every issue's terminals.** Each issue's Claude runs in a terminal of the plan
  window named for its key, and the Issues view switches between them. The window never closes an
  issue's terminals itself, so a done issue's terminals stay until the human closes them.
- **The open-issue action does the per-issue setup.** `plan-window` opens nothing for an issue
  when the window starts. Opening the issue, from the view or the link, starts its Claude terminal
  and a shell and opens its ticket.
- **Issue colours show only in the plan window.** With it closed, nothing shows them; `plan agents`
  still lists every Claude's state.
- **A closed plan window keeps every issue.** The issues file is not swept against the window. The
  next Start or Open reopens it with every issue's folder, and opening an issue that has no
  terminal starts one.
- **Reopening a finished issue is one manual step.** Done removes the key, the pulse and the folder
  and leaves the terminals open. The human sets the issue back to In Progress and runs
  `plan start <KEY>`, which makes the worktree again, adds the key and the folder, and lets the
  Claude terminal resume with `claude --continue` when the worktree's conversation files exist.
- **Docs follow.** The architecture and the failure modes state this decision, and GUIDE follows
  them.
- **Code follows** in SK-0063.
