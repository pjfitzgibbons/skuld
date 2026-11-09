#!/bin/bash
# trial.sh <n> <desktop 1-4> <delay seconds>: switch with ^desktop, wait, code -n, report the new window's Space
S=$(dirname "$0"); n=$1; target=$2; delay=$3
key() { osascript -e "tell application \"System Events\" to key code $((17+$1)) using control down"; }
ids() { "$S/spaces" Code | awk '/^window/ && $NF!="spaces=[]" {print $2}' | sort; }
sid() { "$S/spaces" | awk -v d="Desktop $1:" '$0 ~ d {sub("id=","",$3); print $3}'; }
key 1; sleep 1.5
before=$(ids); want=$(sid $target)
t0=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
key $target; sleep $delay
echo "  after switch+delay active=$("$S/spaces" | awk '/^active/ {print $3}')"
code -n "$S/ws/t$n.code-workspace"
for i in $(seq 1 60); do echo "  t=$i active=$("$S/spaces" | awk '/^active/ {print $3}')"; new=$(comm -13 <(echo "$before") <(ids)); [ -n "$new" ] && break; sleep 0.25; done
t1=$(perl -MTime::HiRes=time -e 'printf "%.3f", time')
got=$("$S/spaces" Code | awk -v w="$new" '$2==w {print $NF}')
active=$("$S/spaces" | awk '/^active/ {print $3}')
echo "trial=$n target=D$target($want) delay=$delay new=$new $got active=$active open_s=$(echo "$t1-$t0" | bc)"
"$S/close-trial.sh" $n $target || exit 1
