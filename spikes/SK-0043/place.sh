#!/bin/bash
# place.sh <desktop> <workspace file>: SK-0043's placement by hand -- ^N, code -n, wait by window id, code <ws>.
S=$(dirname "$0"); d=$1; WS=$2
ids() { "$S/spaces" Code | awk '/^window/ && $NF!="spaces=[]" {print $2}' | sort; }
before=$(ids)
osascript -e "tell application \"System Events\" to key code $((17+d)) using control down"
code -n "$WS"
for i in $(seq 1 60); do new=$(comm -13 <(echo "$before") <(ids)); [ -n "$new" ] && break; sleep 0.25; done
echo "new window: $new $("$S/spaces" Code | awk -v w="$new" '$2==w {print $NF}')"
code "$WS"; sleep 2
"$S/spaces" | sed -n '1,6p'
