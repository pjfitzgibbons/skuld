# Prior art for skuld: open-source trackers, forges and agent orchestrators

Research dated 2026-11-16. It extends the survey of 2026-11-14 (`main-synthesis.md:36-83`). Every
claim below is sourced in one of the round-2 reports in this folder, which carry the URLs:

| Report                      | Covers                                                                                                                                       |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `r2-server-trackers.md`     | Plane CE, OpenProject, Huly, Redmine, Taiga, Leantime, Tuleap, Kanboard, Vikunja, It's a Plan                                                |
| `r2-forges.md`              | Gitea, Forgejo, OneDev, GitLab CE, Phorge, SourceHut, Radicle                                                                                |
| `r2-agent-orchestrators.md` | Emdash, Vibe Kanban, Multica, Agent Orchestrator, Paseo, Backlog.md, Task Master, Claude Squad, Sculptor, Nimbalyst, Cline Kanban, Conductor |
| `r2-bones.md`               | Architecture of the 12-project shortlist, and source checks of It's a Plan's claims                                                          |

Tags: ✅ met · ⚠️ partial · ❌ not met · ? UNVERIFIED.

## 1. Requirements

|       | Requirement                                                                                                        |
| ----- | ------------------------------------------------------------------------------------------------------------------ |
| R1    | Open source, self-hostable server                                                                                  |
| R2    | Multi-project; a project spans several git repos                                                                   |
| R3    | GitHub PR integration: PR on the issue, status from PR events                                                      |
| R4    | Claude integration: MCP server, API, webhooks that can start an agent                                              |
| R5    | VS Code extension                                                                                                  |
| R6    | A git branch and worktree per issue                                                                                |
| R7    | A plugin or extension system that can fill the gaps                                                                |
| R8    | JetBrains plugin (secondary; it was a primary requirement in round 1)                                              |
| Bones | Extension system, data model, storage, API, and openness to outside contributions, judged separately from features |

## 2. Findings

**No project meets every requirement.** The field splits into two layers, and no project with sound
bones covers both:

- **The tracker server:** projects, issues, links, history and PR state. Redmine, OpenProject,
  Gitea, OneDev, Plane and It's a Plan.
- **The agent and worktree layer:** a worktree per task, a Claude Code session per task, and opening
  the task in VS Code. Emdash, Vibe Kanban, Agent Orchestrator and Paseo.

Skuld today is the second layer, plus a file store standing in for the first.

**Multica is the one project that reaches into both layers** (`r2-bones.md`, Multica):

- Projects point to any number of GitHub repos, and adding a new resource type needs no schema
  change. Verified at `server/migrations/065_project_resources.up.sql`: "adding a new type requires
  zero schema changes".
- PRs join issues in `079_github_integration.up.sql`.
- Plugins can add issue-page panels and event hooks.
- Agent tasks carry a branch name.

Its license is Apache-2.0 plus conditions, so it is source-available, not open source (§5).

## 3. Feature fit, by category

### 3.1 Self-hosted trackers (`r2-server-trackers.md`)

| Tool                                       | R1                        | R2  | R3               | R4               | R5           | R6                   | R7  | R8       |
| ------------------------------------------ | ------------------------- | --- | ---------------- | ---------------- | ------------ | -------------------- | --- | -------- |
| It's a Plan                                | ✅ AGPL-3.0               | ?   | ✅               | ✅               | ?            | ?                    | ⚠️  | ?        |
| Redmine                                    | ✅                        | ?   | ⚠️ stale plugins | ✅ community MCP | ⚠️ community | ?                    | ✅  | ⚠️       |
| OpenProject                                | ⚠️ GPL, MCP is Enterprise | ⚠️  | ?                | ⚠️               | ❌           | ✅ branch from issue | ?   | ⚠️ stale |
| Plane CE                                   | ⚠️ open-core              | ?   | ❌ Pro only      | ✅ official MCP  | ❌           | ?                    | ⚠️  | ?        |
| Huly                                       | frozen, unmaintained      |     |                  |                  |              |                      |     |          |
| Taiga, Leantime, Tuleap, Kanboard, Vikunja | each fails R3 or R4       |     |                  |                  |              |                      |     |          |

It's a Plan's claims were checked against its source (`r2-bones.md` §B6), and all were CONFIRMED:

- Its license is AGPL-3.0.
- A merged PR with `Fixes KEY-n` moves the issue to done, in the free edition
  (`apps/api/src/modules/git/handler.ts:175-194`).
- It has an MCP server with 236 tools.
- Its webhooks are signed (`X-Itsaplan-Signature`).
- It has a `claude` agent preset. Agent workflows triggered by issue events are Pro.

### 3.2 Self-hosted forges (`r2-forges.md`)

No forge mirrors GitHub issues or PRs: a mirror carries git data only. With the code staying on
GitHub, any forge needs a webhook relay to link PRs to its issues.

| Tool                       | R1                       | R2                      | R3                        | R4                       | R5          | R6                           | R7  | R8  |
| -------------------------- | ------------------------ | ----------------------- | ------------------------- | ------------------------ | ----------- | ---------------------------- | --- | --- |
| Gitea                      | ✅ MIT                   | ✅ org projects         | ⚠️ git-only mirror        | ✅ official MCP          | ⚠️ official | ⚠️                           | ✅  | ⚠️  |
| OneDev                     | ⚠️ MIT core, paid EE     | ⚠️                      | ⚠️ import only            | ✅ official MCP, AI user | ❌          | ⚠️ `tod issue create-branch` | ✅  | ❌  |
| GitLab CE                  | ⚠️ open-core             | ⚠️ one group board free | ⚠️ pull mirror is Premium | ✅ MCP on Free           | ✅ official | ⚠️                           | ✅  | ✅  |
| Forgejo                    | ✅ GPLv3+                | ?                       | ⚠️                        | ⚠️                       | ⚠️          | ❌                           | ✅  | ?   |
| Phorge, SourceHut, Radicle | each fails R3, R5 and R6 |                         |                           |                          |             |                              |     |     |

### 3.3 Agent orchestrators (`r2-agent-orchestrators.md`)

This category was not in round 1. Each of these runs Claude Code per task in its own git worktree.

| Tool               | R1                          | R2  | R3                       | R4             | R5  | R6  | R7                                                      | R8  |
| ------------------ | --------------------------- | --- | ------------------------ | -------------- | --- | --- | ------------------------------------------------------- | --- |
| Emdash             | ⚠️ Apache-2.0, desktop app  | ?   | ✅                       | ✅             | ✅  | ✅  | ✅ issues from YouTrack, Jira, Plane, GitHub and others | ✅  |
| Multica            | ⚠️ source-available, server | ✅  | ⚠️                       | ✅             | ?   | ⚠️  | ⚠️                                                      | ?   |
| Vibe Kanban        | ✅ Apache-2.0, local web    | ⚠️  | ⚠️                       | ✅ exposes MCP | ✅  | ⚠️  | ⚠️                                                      | ?   |
| Agent Orchestrator | ⚠️ Electron                 | ⚠️  | ✅ best PR feedback loop | ✅             | ?   | ✅  | ⚠️                                                      | ?   |
| Paseo              | ✅ Apache-2.0               | ?   | ✅                       | ✅             | ⚠️  | ✅  | ✅                                                      | ?   |
| Backlog.md         | ✅ MIT                      | ⚠️  | ❌                       | ⚠️ MCP         | ⚠️  | ❌  | ⚠️                                                      | ?   |

- **Emdash's issue sources are read-only.** The `issues` adapters only list, search and get. They
  are compiled into the app, and there is no runtime install path (`r2-bones.md`, Emdash).
- **Vibe Kanban has lost its company.** bloop shut down on 2026-04-10, and the project is now
  community-maintained.

## 4. Bones: a project to contribute to (`r2-bones.md`)

Extension system, data model, storage, API and contribution are graded separately from features.

| Project     | Extension      | Data model           | Storage, ops | API      | Contribution   |
| ----------- | -------------- | -------------------- | ------------ | -------- | -------------- |
| Multica     | strong (young) | strong               | adequate     | adequate | weak (license) |
| Redmine     | strong         | strong               | adequate     | adequate | adequate       |
| OpenProject | strong         | strong               | adequate     | strong   | adequate       |
| OneDev      | adequate       | strong               | adequate     | adequate | adequate       |
| It's a Plan | weak           | strong               | adequate     | strong   | adequate       |
| Gitea       | weak           | adequate             | strong       | strong   | strong         |
| Plane CE    | weak           | adequate             | weak         | adequate | adequate       |
| Forgejo     | weak           | adequate             | strong       | strong   | weak           |
| Emdash      | weak           | weak (not a tracker) | strong       | weak     | adequate       |
| Backlog.md  | weak           | weak                 | strong       | adequate | adequate       |
| Vibe Kanban | weak           | weak                 | strong       | weak     | weak           |
| Huly        | weak           | adequate             | weak         | ?        | weak (frozen)  |

**Ranking:**

1. **Multica.**
   - For: its schema already models the features skuld would add. Projects point to any number of
     repos, PRs link to issues, `issue_property` holds custom fields, and plugins add issue panels
     and event hooks with scoped permissions.
   - Against: the license (§5), and a plugin API that has been reset once (`344_plugin_v2_reset`).
2. **Redmine.**
   - For: the most mature in-process plugin API, typed relations, full change history, any number of
     repos per project, no paid tier and no CLA. The missing features are built as a plugin, so no
     upstream merge is needed.
   - Against: there is no PR model in core, API keys are per user with no scopes, and core patches
     go through redmine.org, not GitHub PRs.
3. **OpenProject.**
   - For: features are Rails engines, so a new one ships as a gem. Work packages have custom fields,
     typed relations and full change history. The GitHub module stores any number of PRs per work
     package, and outside PRs are merged: 45 of the last 53 merged PRs came from outside
     contributors.
   - Against: the official MCP server is Enterprise (UNVERIFIED: from a search summary), so a
     contributed agent feature competes with the paid tier. It also requires a CLA.

**Strong community, weak extension system:**

- **Gitea** merged 385 PRs in 30 days, and its AGENTS.md accepts `Assisted-by` trailers. It has no
  plugin API and no custom fields, so every feature means a core change.
- **It's a Plan** has the best data model for the issue workflow. Against it: its only extension
  interface is hosted-only, it requires an ICLA, and agent workflows are Pro.

**Ruled out:**

- **Forgejo:** "does not accept any works … partially or fully authored by coding agents" (Forgejo
  `CONTRIBUTING.md:7`, verified). Skuld's work is written with Claude.
- **Huly:** "This repository is frozen and is no longer actively maintained" (README:9, verified).

## 5. Multica's license

Verified from `LICENSE` Part I. The full text is in `r2-bones.md`.

- **1a:** without a commercial license, Multica may not provide a hosted service to third parties,
  or be embedded in a product sold or distributed to third parties.
- **1b to 1d:** the UI attribution stays, products built on it credit Multica, and a branding waiver
  and a commercial license are separate grants.
- **2:** contributions are submitted under the whole Multica License. The producer may change the
  license and may use the code commercially. This works as a CLA.

Personal use is not restricted by condition 1a. Whether these terms are acceptable for code you
contribute is your decision.

## 6. Decisions for you

1. **Accept source-available terms or not.** If yes, Multica is the strongest base. If no, Redmine
   is: build skuld's features as a Redmine plugin, plus a VS Code and worktree layer that calls its
   API.
2. **Keep the agent and worktree layer as skuld's own, or adopt Emdash.** Emdash already does a
   worktree per task, PR and CI links, Claude Code status, and opening in VS Code or JetBrains. Its
   tracker adapters are read-only and compiled into the app, so a Redmine or Multica adapter would
   be a contribution to Emdash.

A spike that runs Multica and Redmine locally against one skuld project would settle the first
decision on evidence.

## 7. Unverified

- Multi-repo projects for Plane, Redmine, Taiga, It's a Plan, Emdash, Agent Orchestrator, Paseo and
  Backlog.md.
- Whether OpenProject's GitHub integration is in Community, and whether its MCP server is
  Enterprise-only.
- Whether Plane's MCP server and apps work on self-hosted CE.
- Whether Multica has typed dependency links, scoped tokens or plain-text export, and whether it
  exposes an MCP endpoint for issues.
- OneDev's EE feature split, and whether its MCP server is EE-only.
- Memory and CPU footprint on a Mac for everything except OneDev and OpenProject.
- The full list of unverified claims per tool is at the end of each round-2 report.
