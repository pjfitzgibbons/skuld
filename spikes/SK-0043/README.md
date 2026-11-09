# SK-0043 spike: issue windows on native macOS Spaces

Throwaway helpers behind SK-0043's Log, kept for the `SpacesPlacer` ticket to build on. They're
not wired into `plan`, and nothing here is tested.

| File | What it does |
|---|---|
| `spaces.swift` | Reads Desktops, the active Space and each window's Space through the read-only SkyLight calls. `spaces [owner]` |
| `trial.sh` | Item 1: ^N, then `code -n` on a trial workspace, then reports the new window's Space. `trial.sh <n> <desktop> <delay>` |
| `close-trial.sh` | Closes only the window titled `SK0043-trial-<n>`, by its AX close button, on its Desktop. |
| `drag.swift`, `move-trial.sh` | Item 3: title-bar drag plus ^N to move a window (it worked 1 time in 13). |
| `menubar/slotbar.swift` | Item 5: the SlotBar menu-bar extra. `slotbar <slots file> <workspace dir>` |
| `menubar/pulse.swift` | Item 5: `pulse <slot> <red\|green>` posts the `skuld.pulse` distributed notification. |
| `menubar/SlotBar.app/Contents/Info.plist` | The bundle SlotBar needs before macOS will place its item. |

## Build and run

```sh
swiftc -O -o spaces spaces.swift
swiftc -O -o drag drag.swift
# The scripts expect trial workspaces in ws/, each titled SK0043-trial-<n> and opening the folder ws/empty:
mkdir -p ws/empty
for i in $(seq 1 50); do
  printf '{"folders":[{"path":"empty"}],"settings":{"window.title":"SK0043-trial-%s"}}' $i > ws/t$i.code-workspace
done

cd menubar
swiftc -O -o slotbar slotbar.swift
swiftc -O -o pulse pulse.swift
mkdir -p SlotBar.app/Contents/MacOS && cp slotbar SlotBar.app/Contents/MacOS/SlotBar
codesign -s - --force SlotBar.app
open -n SlotBar.app --args "$PWD/slots" "$PWD"   # slots: lines "N=KEY"; workspaces: KEY.code-workspace
```

The settings each finding assumes are in SK-0043's Log.
