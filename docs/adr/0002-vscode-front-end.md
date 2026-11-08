# ADR 0002: Work each issue in one VS Code window

<a id="status"></a>

## 1. Status

Accepted.

<a id="context"></a>

## 2. Context

Start and Open gave each issue a Ghostty window on its own Aerospace workspace, running a tmux
session with a Claude window and a shell window. A store ticket opened in nvim, in a tmux pane
beside Claude, and pages opened as tabs in a Brave window that `openUrl()` found or made on the same
workspace through AppleScript. That is four programs per issue: a terminal, a multiplexer, an
editor and a browser. Each needs its own window placement, focus handling and config, and pages
and links had to be steered into the right Brave window by hand-written AppleScript.

VS Code holds all four in one window: an editor, integrated terminals and an integrated browser.
An earlier attempt to work in VS Code was dropped for two reasons:

- **Too much ran behind extensions.** One button did everything, so a step that failed left
  nothing the human could see, repeat or do by hand.
- **The issue tracker lived inside the code project.** Tickets were files in the development repo,
  so the editor, the tracker and the code shared one tree and one history.

Since then the tracker has moved to a ticket store of its own, behind the `IssueStore` port
([ADR 0001](0001-file-ticket-store.md)).

<a id="decision"></a>

## 3. Decision

Each issue gets one VS Code window on its Aerospace workspace. The window replaces Ghostty, tmux,
the nvim ticket pane and Brave.

- **One window, one multi-root workspace.** Start writes `<repo>.worktrees/<KEY>.code-workspace`,
  beside the worktree and outside every repo, listing the issue's worktree and the ticket store, and
  opens it in a new window with the `code` CLI. The window moves to the issue's workspace the way
  Ghostty's did.
- **Claude and a shell run in integrated terminals.** Start starts Claude with its prompt in one
  terminal and leaves a shell in another. The terminals carry the issue's key in their environment,
  which takes the place of the tmux session name.
- **Pages open in the window's integrated browser.** `openUrl()` and links clicked in a terminal
  open there, in the issue's own window.
- **A store ticket opens in an editor tab,** from the store folder of the same workspace.
- **Start drives VS Code from outside, through `code` and the workspace file.** No extension
  carries Start's logic. Every step is one the human can do by hand, so a failed step leaves a
  usable window and a message naming what to do. How Start starts Claude and opens a page is
  settled by SK-0037, with the least extension code that works.
- **Aerospace, the slots file and SketchyBar stay.** Workspaces, slots and the bar work as before;
  only the window on the workspace changes.
- **The way back** is the `archive/ghostty-tmux-brave` tag, in skuld and in the ticket store. It
  marks the docs and code from before this change.

<a id="alternatives"></a>

## 4. Alternatives considered

- **Keep Ghostty, tmux, nvim and Brave.** It works, but four programs per issue each need
  placement and focus handling, and steering a page into the issue's Brave window takes AppleScript
  that can't tell two windows with the same title apart.
- **A VS Code extension that runs Start, Open and Ship from inside the editor.** It is one button,
  which is what made the earlier attempt brittle: a failure inside the extension leaves nothing to
  do by hand. `plan` stays a terminal command, and VS Code is a window it opens.
- **One VS Code window for every issue,** with a folder per worktree. Aerospace workspaces, the
  slots file and SketchyBar all place one issue per workspace, and one window can't sit on several.
- **Tickets in the development repo, opened as part of the worktree.** That is the other cause of
  the earlier friction; the store stays its own repo, added to the window as a second root.

<a id="consequences"></a>

## 5. Consequences

- **What goes.** Ghostty, tmux and Brave are no longer needed by skuld. The `ghosttyApp`,
  `browserApp` and `browserBundleId` config keys are removed, and an unknown key in an old config
  is ignored. nvim is no longer opened by Start.
- **A session started under tmux is finished by hand.** Start and Open don't look for a tmux
  session or a Ghostty window. An issue started before the change keeps its worktree, branch and
  slot; Open on it opens a VS Code window on the same workspace. Its old tmux session and Ghostty
  window are left to the human to close (`tmux kill-session -t <KEY>`). Ship and Done work on the
  worktree and branch, and don't need either.
- **Workspace trust.** The worktree and the store have to be trusted for a task that runs when the
  window opens; SK-0037 says how narrow that trust can be.
- **Done removes the workspace file** with the worktree.
- **Ship's output** shows in an integrated terminal of the issue's window instead of a tmux window.
- **Docs follow.** The architecture and GUIDE describe the VS Code window, its terminals and its
  integrated browser, and name no Ghostty, tmux or Brave behaviour.
- **Code follows** in SK-0030 (the window) and SK-0031 (`openUrl()`), once SK-0037 has found how
  to start Claude and open a page.
