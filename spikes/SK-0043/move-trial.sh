#!/bin/bash
# move-trial.sh <n> <desktop>: open trial window n on Desktop 1, try to move it to <desktop>; report its Space; close it.
S=$(dirname "$0"); n=$1; d=$2
key() { osascript -e "tell application \"System Events\" to key code $((17+$1)) using control down"; }
key 1; sleep 1.5
code -n "$S/ws/t$n.code-workspace"; sleep 4
frame=$(osascript -e "tell application \"System Events\" to tell (first process whose bundle identifier is \"com.microsoft.VSCode\") to get {position, size} of (first window whose name contains \"SK0043-trial-$n\")" 2>&1) || { echo "no frame: $frame"; exit 1; }
read x y w h <<<"$(echo $frame | tr -d ',')"
wid=$("$S/spaces" Code | awk '/^window/ && $NF!="spaces=[]"' | head -1 | awk '{print $2}')
px=$((x + w * 4 / 5)); py=$((y + 12))
"$S/drag" $px $py $d; sleep 1.5
active=$("$S/spaces" | awk '/^active/ {print $3}')
echo "move trial $n -> D$d: frame=$frame grab=($px,$py) active=$active"
"$S/spaces" Code | awk '/^window/ && $NF!="spaces=[]"'
