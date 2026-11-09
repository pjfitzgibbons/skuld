# Moving skuld to another Mac

This guide sets up skuld on a Mac that already runs the dotfiles (`pjfitzgibbons/dotfiles`,
managed by chezmoi), so that Mac can run `plan` and `pr`, and pick up `SK-` issues from the ticket
store as well as its own Jira issues. It was written for the work Mac, which comes to skuld from
gh-plan and from its own fork of jira-plan (skuld's predecessor, since merged into skuld). It
leaves AeroSpace and Neovim behind: windows go on macOS Desktops, and VS Code is the editor.
[GUIDE.md](GUIDE.md) §2 is the general setup and says what each piece does; this guide is the
order to do it in, and it says where GUIDE §2 describes something `main` doesn't do yet.

Work through it top to bottom. Each step ends with a check.

## Contents

1. [What this Mac ends up with](#1-what-this-mac-ends-up-with)
2. [Before you start](#2-before-you-start)
3. [Pull and apply chezmoi](#3-pull-and-apply-chezmoi)
4. [What chezmoi doesn't do](#4-what-chezmoi-doesnt-do)
5. [The machine config](#5-the-machine-config)
6. [Claude in each configured repo](#6-claude-in-each-configured-repo)
7. [Check the setup](#7-check-the-setup)
8. [Retire gh-plan, jira-plan and AeroSpace](#8-retire-gh-plan-jira-plan-and-aerospace)
9. [Working the issues from two Macs](#9-working-the-issues-from-two-macs)
10. [Picking up an SK issue](#10-picking-up-an-sk-issue)
11. [What `main` does and GUIDE §2 doesn't match](#11-what-main-does-and-guide-2-doesnt-match)
12. [Troubleshooting](#12-troubleshooting)

## 1. What this Mac ends up with

```text
~/programming/skuld-project/
  skuld/                   skuld's main checkout (github.com/pjfitzgibbons/skuld)
  skuld.worktrees/<KEY>/   one worktree per started SK issue, made by plan start
  issues/                  the ticket store (github.com/pjfitzgibbons/issues, private)
  skuld.code-workspace     opens skuld and the store together

~/.local/bin/              plan, jira-get, pr, new_issue, rebase-main  (symlinks)
~/.claude/                 CLAUDE.md, rules/, agents/, skills/          (chezmoi)
~/.claude/skills/jira-issues -> skuld/skills/jira-issues                 (symlink)
~/.vscode/extensions/skuld.plan-window-0.0.1 -> skuld/vscode/plan-window (symlink)
~/.config/skuld/config.json  this Mac's skuld config                     (yours to edit)
~/.config/sketchybar/        a bar of macOS Desktops, with the issue on each (chezmoi)
~/.netrc                     the Jira API token                          (yours, never tracked)
```

Each Jira repo you work in gets `<repo>.worktrees/<KEY>/` beside it, the same way. jira-plan used
the same layout, so its worktrees for in-flight issues carry straight over.

## 2. Before you start

- **Finish or push work in progress on the other Mac.** An issue's branch is only visible here once
  it's on `origin`. Push any branch you want to carry on with, then push the ticket store
  (`git -C ~/programming/skuld-project/issues push`).
- **Check the jira-plan fork for unpushed work.** In the fork's checkout, `git status` and
  `git log --branches --not --remotes --oneline` should both come back empty. Anything they show
  that skuld doesn't have yet becomes an SK issue or a PR to skuld before §8 retires the fork.
- **GitHub over SSH.** The dotfiles and skuld clone with `git@github.com:...`. Check with
  `ssh -T git@github.com`. The account needs access to `pjfitzgibbons/skuld`,
  `pjfitzgibbons/issues` (private) and the work repos.
- **Homebrew** at `/opt/homebrew`.
- **VS Code**, with its `code` command on `PATH` (Command Palette: *Shell Command: Install 'code'
  command in PATH*). Brew bundle installs extensions through `code`, so it has to be there first.
- **Claude Code's CLI** as `claude` on `PATH`. The plan-window extension starts it by name in a
  login shell. Install it with `curl -fsSL https://claude.ai/install.sh | bash` if it's missing,
  then sign in by running `claude` once.
- **Know the AeroSpace gap.** Skuld on `main` places each issue's VS Code window on an AeroSpace
  workspace, and this Mac won't run AeroSpace. SK-0044 places it on a macOS Desktop instead, and
  is in progress. Until it merges, Start and Open do all their other work, then stop at
  *placing the issue on an Aerospace workspace failed* and print `code -n <workspace file>`. Run
  that to open the window (§10).

## 3. Pull and apply chezmoi

```sh
chezmoi git pull
chezmoi diff        # read it before applying; see below
chezmoi apply
```

Read the diff before applying. This apply is the first to manage these, and it overwrites them
without asking:

- `~/.claude/CLAUDE.md`, `~/.claude/rules/`, `~/.claude/agents/`, and the `clean-code`,
  `clean-architecture`, `humanizer` and `rebase-main` skills. If this Mac's `CLAUDE.md` has
  work-only lines, copy them somewhere first and put them back afterwards (or into the work repo's
  own `CLAUDE.md`).
- `~/.local/bin/plan`, `pr`, `jira-get`, `new_issue` and `rebase-main`, which become symlinks. If
  any is a different tool here, rename it before applying.

`~/.claude/settings.json` isn't managed; §4 covers it.

The apply also brings the rest of the dotfiles up to date:

- SketchyBar's bar shows macOS Desktops instead of AeroSpace workspaces, labelled with the issue
  on each one, and `plugins/workspaces.sh` is removed.
- The karabiner rule that turns Left Ctrl into ctrl+opt for AeroSpace is personal-only, so Left
  Ctrl is plain Control again here.
- `EDITOR` is `code --wait`, since nvim isn't installed.
- VS Code gets tmux-style pane keys in `keybindings.json` (opt-hjkl to move, cmd-d to split),
  opens Markdown in the plain editor, and drops gh-plan's `plan.checkouts` settings.

`.aerospace.toml`, `~/.config/nvim` and `aerospace-keys` still deploy, unused, because the dotfiles
send every file to both Macs.

The apply then runs three scripts:

| Script | Does |
| --- | --- |
| `10-brew-bundle` | `brew bundle --global`: the shared Brewfile plus `Brewfile.work`. The Brewfile now includes `python`, since `plan` needs Python 3.10 or later and `/usr/bin/python3` is 3.9. AeroSpace and Neovim are in `Brewfile.personal` now, so it doesn't install them here |
| `20-setup-mac` | Sets the Nerd Font in iTerm and Terminal.app |
| `30-skuld` | Clones `skuld` and `issues` into `~/programming/skuld-project/`, writes `skuld.code-workspace` and a starting `~/.config/skuld/config.json`, and imports skuld's gh aliases. It adds only what's missing |

The first two run again whenever the file they follow changes; `30-skuld` runs on every apply. A
script that fails stops the apply after the files are written, and runs again next time. If a clone
fails, fix SSH access and run `chezmoi apply` again.

**Check:**

```sh
ls ~/programming/skuld-project            # issues  skuld  skuld.code-workspace
readlink ~/.local/bin/plan                # .../skuld-project/skuld/bin/plan
readlink ~/.claude/skills/jira-issues     # .../skuld-project/skuld/skills/jira-issues
python3 --version                         # 3.10 or later
type -a pr                                # ~/.local/bin/pr before /usr/bin/pr
```

## 4. What chezmoi doesn't do

1. **Sign in to gh**, if `30-skuld` said it wasn't: `gh auth login`, then `chezmoi apply` again to
   import `gh pc` and `gh pb`. Check with `gh alias list`, which should show `pc`, `pb`, `co` and
   `squash`.

2. **The Jira token**, in `~/.netrc` only: never in a tracked file or an environment variable.

   ```text
   machine cryoport.atlassian.net
     login <your Atlassian email>
     password <API token>
   ```

   Then `chmod 600 ~/.netrc`. Check with `jira-get /rest/api/3/myself`, which prints your Jira
   user as JSON.

3. **Claude Code's global settings.** In `~/.claude/settings.json`, set the commit trailer and turn
   off the PR and session lines (the global `CLAUDE.md` relies on this):

   ```json
   "attribution": {
     "commit": "Assisted-by: Claude:claude-opus-5-5",
     "pr": "",
     "sessionUrl": false
   }
   ```

   The other Mac's hooks (iTerm status, tempotag) point at tools this Mac may not have; leave
   them out.

4. **Restart VS Code**, so it loads the plan-window extension. *Extensions* should list
   **plan window**. It's disabled in Restricted Mode, so trust these folders when VS Code asks, or
   add them under *Workspace Trust*:
   - `~/programming/skuld-project` (skuld, its worktrees and the store)
   - the parent folder of each work repo, since its `<repo>.worktrees/` sits beside it

5. **Claude's memory for skuld** (optional). The other Mac's Claude has notes on how skuld's work
   is done, such as squash-merging PRs, regenerating `INDEX.md` rather than editing it, and letting
   the issue window's Claude do the issue's work. Claude keeps them per project folder, under a
   name made from the folder's path. To bring them over:

   ```sh
   from=-Users-pjfitzgibbons-programming-skuld-project-skuld      # the other Mac's folder name
   to=$(echo "$HOME/programming/skuld-project/skuld" | tr / -)   # this Mac's
   mkdir -p ~/.claude/projects/$to/memory
   scp -r "<other mac>:.claude/projects/$from/memory/" ~/.claude/projects/$to/
   ```

   The memory index, `MEMORY.md`, names its files relatively, so it works under the new folder name.

## 5. The machine config

`30-skuld` wrote `~/.config/skuld/config.json` with only `issueStore` in it. Add this Mac's Jira
repos. jira-plan's `~/.config/jira-plan/config.json` uses the same `checkouts` schema, so copy its
`CIAM` entry, and any `jql` or `typeMap` you set there. Leave out its `JP` entry; skuld has no JP
project.

```json
{
  "issueStore": "~/programming/skuld-project/issues",
  "checkouts": {
    "SK": ["~/programming/skuld-project/skuld"],
    "CIAM": [
      "~/programming/cryo-gitops-github",
      "~/programming/CryoportSystems/Cryoport-Authentication-Service"
    ]
  }
}
```

- **`checkouts` replaces the default; it doesn't add to it.** The default maps `SK` to skuld's own
  checkout. A config that sets `checkouts` without an `SK` entry loses that mapping, so keep the
  `SK` line.
- `checkouts` is a fallback. `plan start` uses the repo you run it from, or the one that already has
  the issue's worktree or branch, and asks with fzf when a project lists more than one repo.
- The schema `main` reads is `{ "<project>": [paths] }`. GUIDE §2's example shows a flat list of
  checkouts plus a `jira` block, which is the design and doesn't work yet (§11).
- The other keys have working defaults: `jql` (your open CIAM issues by rank), `typeMap`, `pr`,
  `aerospaceConfig` (`~/.aerospace.toml`), `slotsFile` (`~/.cache/plan/workspaces`),
  `browserApp` (Brave). `lib/config.py` lists them all.

**Check:** `plan list` prints Next up, with the SK issues and your CIAM issues merged.

## 6. Claude in each configured repo

Skuld's hooks and settings go into each work repo Claude works in (GUIDE §9). For each one:

```sh
repo=~/programming/cryo-gitops-github
skuld=~/programming/skuld-project/skuld
mkdir -p "$repo/.claude"
cp -R "$skuld/claude/hooks" "$repo/.claude/"
cp "$skuld/claude/settings.json" "$repo/.claude/settings.json"
cp "$skuld/claude/CLAUDE.md" "$repo/CLAUDE.md"
printf '.claude/\nCLAUDE.md\n' >> "$repo/.git/info/exclude"
```

The last line keeps them out of the team's repo; nothing here is for committing to a work repo. If
the repo already tracks a `.claude/` or `CLAUDE.md` of its own, don't overwrite it; merge skuld's
hooks into it by hand instead.

A worktree doesn't get ignored files, and Start on `main` doesn't copy them in. The issue
window's Claude runs in the worktree and reads the hooks from `$CLAUDE_PROJECT_DIR/.claude/`.
So after each Start in a work repo, copy them across before Claude does anything:

```sh
cp -R "$repo/.claude" "$repo/CLAUDE.md" "$repo.worktrees/<KEY>/"
```

Skuld's own checkout and worktrees run without these on the other Mac too. The global
`~/.claude/` from chezmoi covers them.

**Check:** in the repo, `python3 .claude/hooks/no_ai_attribution.py --check 'git commit -m "x"'`
refuses, naming the missing `Assisted-by:` line.

## 7. Check the setup

Run these in order; each depends on the one before.

| Check | Expect |
| --- | --- |
| `plan list` | Next up, SK and CIAM together |
| `plan view SK-0001` | The ticket's fields and body, from the store |
| `plan view CIAM-<n>` | A Jira issue, through `~/.netrc` |
| `plan` | The fzf picker, with the issue's detail in the preview |
| `pr --help 2>&1 \| head -3` | skuld's `pr`, not the Unix paginator |

Then try a real Start on a small SK issue (§10) and check the issue window's Claude starts in its
terminal.

## 8. Retire gh-plan, jira-plan and AeroSpace

Once `plan` works here. brew bundle installs what the Brewfiles list but never removes anything,
so the old tools stay until you remove them.

**gh-plan.** Uninstall its VS Code extensions, and archive or delete its checkout
(`~/programming/gh-plan`) once nothing there is unpushed:

```sh
code --uninstall-extension pjfitzgibbons.gh-plan
code --uninstall-extension pjfitzgibbons.worktask
```

**The jira-plan fork.** Once §2's check found nothing unpushed:

1. Finish or move its in-flight issues. Their worktrees are in skuld's layout already, so
   `plan open CIAM-<n>` takes one over in a VS Code window. Its tmux session and Ghostty window
   are jira-plan's; close them with `tmux kill-session -t =CIAM-<n>`.
2. Remove what pointed at the fork: anything in `~/.local/bin` that links into it (chezmoi's apply
   has already replaced `plan`, `jira-get` and `pr`), and any `plan`, `pr` or `plan-open`
   function or alias in files chezmoi doesn't manage (`~/.bash_profile.local`, `~/.zshrc.local`).
   `type -a plan pr jira-get` should name only `~/.local/bin` links into skuld (`pr` into the
   jira-issues skill).
3. Delete `~/.config/jira-plan/` after §5 has copied what you need from it, then archive or
   delete the fork's checkout.

**AeroSpace and Neovim.** Quit AeroSpace and take it out of Login Items, then:

```sh
brew uninstall --cask aerospace
brew uninstall neovim          # optional; nothing here uses it
rm -f ~/.cache/plan/workspaces # slots naming AeroSpace workspaces, from earlier plans
```

**The Desktop bar** (optional). Turn on *Switch to Desktop 1–9* (⌃1–⌃9) under Keyboard
Shortcuts → Mission Control, give SketchyBar Accessibility access, and run
`brew services start sketchybar`. After adding or removing a Desktop, run `sketchybar --reload`.

## 9. Working the issues from two Macs

The ticket store is a git repo, and nothing on `main` syncs it yet. GUIDE §2's `issue-watch`
LaunchAgent is the design, not the code. Until it exists, the store is pulled and pushed by hand,
and both Macs share one rule: **pull before you change the store, push when you're done.**

```sh
git -C ~/programming/skuld-project/issues pull --rebase
# plan start, edit tickets, plan new ...
python3 ~/programming/skuld-project/issues/scripts/index.py   # see below
git -C ~/programming/skuld-project/issues add -A
git -C ~/programming/skuld-project/issues commit -m "..."
git -C ~/programming/skuld-project/issues push
```

- **Never edit `INDEX.md` by hand.** After a ticket's `status`, `ordinal`, `milestone` or
  `blocked_by` changes (Start changes `status`), run `python3 scripts/index.py` in the store and
  commit what it writes. `scripts/index.py --check` fails while it's stale.
- **One issue, one Mac at a time.** Start marks the ticket `in-progress` in the store. If the other
  Mac has started it, pull first: Start then becomes Open, and it reuses the branch from `origin`
  when the other Mac has pushed it.
- **Skuld itself** changes only by PR to `main`, squash-merged. Pull `skuld/` on each Mac after a
  merge: `git -C ~/programming/skuld-project/skuld pull`. Since `plan`, the skill and the extension
  are symlinks into it, the pull is the whole update. Restart VS Code if `vscode/plan-window/`
  changed.
- **Dotfiles** go the same way: `chezmoi update` pulls and applies.

## 10. Picking up an SK issue

1. Pull the store (§9) and look at the order: `plan`, or `issues/INDEX.md`. An issue is ready when
   nothing it's `blocked_by` is still open.
2. Start it: `plan start SK-<n>`, from anywhere. SK issues map to skuld's checkout through
   `checkouts`. Start:
   - checks blockers and asks before going on when one is open
   - makes `feat/SK-<n>-<slug>` off `main` (or reuses the issue's branch from `origin`) in
     `skuld.worktrees/SK-<n>/`
   - sets the ticket to `in-progress` and adds a `## Log` line
   - writes `skuld.worktrees/SK-<n>.code-workspace` and opens its VS Code window. Until SK-0044
     merges, this step stops with an error ending `Open it by hand: code -n <workspace file>`.
     Everything before it is done, so run that command
3. The issue window opens with the ticket in a tab, Claude in a terminal started with *"Work on
   SK-<n>. It is a local file-backed issue … read <ticket> first"*, and a shell in the panel. The
   issue's work happens there, with that Claude, not from another session.
4. Push the store now, so the other Mac sees the issue is taken (§9).
5. When Claude has finished, it has written the round's files into the worktree's
   `.tmp/SK-<n>/`: `commit.txt` and `pr-body.md`, plus `replies/` in a review round. It has also
   ticked the ticket's `## Done` boxes. Read them, then **Ship**: `plan ship` in the worktree, or
   `ctrl-s` in the picker. Ship checks the files and runs `pr`, which commits, pushes, and opens or
   updates the PR.
6. After review, squash-merge on GitHub (`gh squash <n>` in the worktree). Then **Done**: `plan done
   SK-<n>` removes the worktree and branch and logs the merge in the ticket. Done never closes the
   issue; set `status: done` and `closed:` in the ticket yourself, run `index.py`, and push the
   store.

A Jira issue works the same way, except Start moves it to *In Progress* in Jira and opens its
browse URL in Brave beside the window, and Claude ticks the Done boxes in `.tmp/<KEY>/issue.md`
for `pr` to send.

**Handoff notes.** Some tickets carry a `## Handoff` section and a WIP branch (SK-0045 does). Start
reuses the branch, and the issue window's Claude reads the Handoff section with the rest of the
ticket.

## 11. What `main` does and GUIDE §2 doesn't match

GUIDE describes the design, and the code catches up through tickets. As of this guide, these parts
of GUIDE aren't in `main`:

| GUIDE says | `main` does |
| --- | --- |
| `checkouts` is a list, plus a `jira` block | `checkouts` is `{ "<project>": [paths] }`; no `jira` key |
| `store.yml` lists projects and repos | No `store.yml`; SK maps to skuld's checkout by default |
| `issue-watch` and the local server run as LaunchAgents | Neither exists; the store is synced by hand (§9) |
| `plan new`, `plan set`, `plan milestone`, `plan browse`, `plan serve`, `plan archive`, `plan init-store`, `issue-check` | Not commands yet. `plan` knows `list`, `view`, `start`, `open`, `ship` and `done` |
| Round files in `<store>/reviews/<KEY>/` | Round files in the worktree's `.tmp/<KEY>/`, which git ignores |
| Issue windows on native macOS Desktops | AeroSpace only; SK-0044 adds Desktops, and SK-0045 a menu-bar list of them |

When one of these lands, its ticket updates GUIDE, and this table should lose a row.

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `chezmoi apply` stops at `10-brew-bundle` | A cask or extension failed; the output names it. `code` must be on `PATH` for the `vscode` lines. Fix it and apply again |
| `30-skuld`: `Permission denied (publickey)` | SSH to GitHub isn't set up for this Mac; add the key, then apply again |
| `plan`: `SyntaxError` or an f-string error | `python3` is `/usr/bin/python3` (3.9). Put `/opt/homebrew/bin` first on `PATH` |
| `plan`: `FileIssues needs the issue store's folder (issueStore)` | `issueStore` is missing from `~/.config/skuld/config.json` |
| `plan start SK-<n>` asks for a repo or refuses | The config's `checkouts` has no `SK` entry (§5) |
| Jira calls fail with 401 | `~/.netrc`'s login is your Atlassian email and its password is an API token, not your password |
| The issue window opens but no Claude terminal appears | The plan-window extension isn't loaded (restart VS Code), or the folder isn't trusted, or `claude` isn't on a login shell's `PATH` |
| `plan start` or `plan open` stops at *placing the issue on an Aerospace workspace* | Expected without AeroSpace until SK-0044 merges. Run the `code -n` command it prints |
| `plan` fails in `sweepSlots` with `aerospace` not found | `~/.cache/plan/workspaces` still lists AeroSpace workspaces. Delete it (§8) |
| `pr` runs the Unix paginator | `~/.local/bin` isn't first on `PATH`. Ship calls `pr` by path, so only typing `pr` by hand is affected |
| `git push` from the issue window's Claude is refused | By design: the repo settings deny it. Ship's `pr` pushes |
