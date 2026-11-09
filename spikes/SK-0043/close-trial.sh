#!/bin/bash
# close-trial.sh <n> <desktop>: go to the Desktop, close only the window titled SK0043-trial-<n>, return to Desktop 1.
# Exits 1 without touching anything when no window has that title.
n=$1; d=$2
key() { osascript -e "tell application \"System Events\" to key code $((17+$1)) using control down"; }
key $d; sleep 1.5
r=$(osascript <<OSA
tell application "System Events"
  set p to first process whose bundle identifier is "com.microsoft.VSCode"
  set ws to (every window of p whose name contains "SK0043-trial-$n")
  if (count of ws) is not 1 then return "skip: " & (count of ws) & " windows match"
  set w to item 1 of ws
  set t to name of w
  set b to (first button of w whose subrole is "AXCloseButton")
  click b
  return "closed: " & t
end tell
OSA
)
echo "close trial $n: $r"
key 1; sleep 1
case "$r" in closed*) exit 0;; *) exit 1;; esac
