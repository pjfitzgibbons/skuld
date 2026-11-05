<!-- markdownlint-disable MD013 -->
# jira-plan: work order

The order to take the open issues in, and why. Issue details live in each `JP-<n>-*.md` file. When
an issue closes, strike it here (`~~JP-n~~`) and move on; re-order only when a finding changes the
reasoning below.

| # | Issue | Type | Why here |
| --- | --- | --- | --- |
| 1 | JP-3 + JP-4 | live check | Today's Ghostty launch, JP-1 reattach, and claude-window focus changes are unit-tested only. One live `plan open CIAM-163` also exercises JP-4: Brave is already running, so `openUrl()` takes the unverified `open -a` new-tab branch. Everything after this assumes Open works. |
| 2 | JP-2 | small bug | One-line usage-message fix in `lib/cli.py`; do it while in the Open code. |
| 3 | JP-7 | task | Open recovers any missing piece of a partial setup. Its tests and design build on what step 1 shows about the real Ghostty/tmux/Aerospace behavior, including the open question of how to tell which Ghostty window belongs to the session. |
| 4 | JP-8 | live check | A human-run GET of one To Do CIAM issue's `/transitions`. Quick, and it confirms Start's only Jira write before the next real Start. |
| 5 | JP-6 | live check | Ship, a review round, merge, and Done end to end. CIAM-163 is the natural candidate: it is started and already has PR #105. Comes after steps 1–4 so a failure here is about Ship/Done, not Open or Start. Findings become new JP issues. |
| 6 | JP-9 | doc/test | Fix `docs/failure-modes.md:17`'s stale reference and map each *Fails* cell to a Python fault-injection test. No dependency on the others; it's last because nothing is blocked on it. |
| — | JP-5 | question | Deferred: `docs/architecture.md:219` says "deferred, not answered, pending a real case." Take it up when an issue actually needs two repos. |

Done: ~~JP-1~~ (2026-11-05).
