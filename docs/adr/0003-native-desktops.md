# ADR 0003: Place issue windows on native macOS Desktops

<a id="status"></a>

## 1. Status

Replaced by [ADR 0004](0004-one-plan-window.md). It replaced the Aerospace placement in
[ADR 0002](0002-vscode-front-end.md), §3.

<a id="context"></a>

## 2. Context

Each issue's VS Code window sat on its own Aerospace workspace. Aerospace holds every window on
one macOS Space and shows a workspace by moving the others' windows off screen, so it has to run all
the time and places every app's windows, with its own config and hotkeys. Native Desktops come with
macOS and its "Switch to Desktop N" shortcuts.

SK-0043 checked what native Desktops allow with SIP on and no yabai scripting addition:

- The ^N shortcuts, sent through System Events, switch Desktops reliably.
- A window opened with `code` opens on the Desktop that is showing.
- The read-only SkyLight calls, resolved with `dlsym`, list the Desktops, the one showing and each
  window's Desktop.
- No public call creates a Desktop, and moving an open window to another Desktop failed 1 time in
  13.

<a id="decision"></a>

## 3. Decision

Issue windows go on native macOS Desktops, and Aerospace is no longer used.

- **A slot is a Desktop number,** counted the way the ^N shortcuts count them. The slots file keeps
  its `<slot>=<KEY>` format, and `windowsFile` records each issue's window id.
- **Placement sits behind the `WindowPlacer` port.** `SpacesPlacer` is its one adapter. Start and
  Open talk to the port, so tests use a fake placer and need no window server.
- **To place a window, switch first, then open.** The placer switches to a free Desktop with its ^N
  shortcut, runs `code` on the workspace file and finds the new window by its id. An open window is
  never moved: when it sits on another Desktop, its slot is rewritten to that Desktop instead.
- **`skuld-spaces`,** a Swift helper built with `swiftc` alone, does the SkyLight reads. A missing
  symbol makes it report "unknown", and Start stops rather than guess.
- **SketchyBar shows native Desktops** with its own `space` items, labelled from the slots file.

<a id="alternatives"></a>

## 4. Alternatives considered

- **Keep Aerospace.** It places and moves windows reliably, but it has to run all the time and
  places every app's windows, not only the issue windows.
- **yabai with its scripting addition.** It can create Desktops and move windows, but only with
  SIP partly off.
- **Keep both behind the port, picked by a config key.** Two adapters double the placement code and
  its tests, for a window manager that is no longer used.

<a id="consequences"></a>

## 5. Consequences

- **What goes.** `AerospacePlacer`, the Aerospace calls in `lib/term.py`, and the `aerospaceConfig`
  and `windowPlacer` config keys. An old config that sets either key still loads; the key is
  ignored.
- **Desktops are set up by hand.** `plan` can't create a Desktop, so the human adds them in Mission
  Control and turns on their ^N shortcuts (GUIDE §2). With no free Desktop, Start and Open refuse.
- **A misplaced window stays where it is.** The slot follows the window, and a window on another
  issue's slot is reported so the human can drag it.
- **An old slots file is mostly swept away.** The first sweep drops a line for a lettered
  Aerospace workspace. A numbered line stays only while that Desktop holds a VS Code window, and the
  next Start or Open for its issue rewrites it.
- **Pages still open in a browser window,** found or made on the issue's Desktop, until SK-0031
  moves them into the integrated browser.
