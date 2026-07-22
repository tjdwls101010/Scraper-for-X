# Harness Spec — agentic-x

## Context

Python 3.11+ package (`agentic-x`, CLI `agentic-x`), hatchling build, pytest + ruff, single maintainer. Published to PyPI; v0.3.0 shipped 2026-07-20. The repo already has a CLAUDE.md of general behavioral guidelines (identical to its scraper-for-fb sibling) and an empty `.claude/skills/x/` directory. No harness-spec.md existed before this pass.

The user is comfortable with Claude Code vocabulary (skills, hooks, CLI catalogs) and reasons about design tradeoffs directly — one of this pass's decisions came from them pushing back on a recommendation and being right. Interview in Korean; generated harness in English (matches the sibling skill, CLAUDE.md, and all CLI output).

**Sibling precedent:** `agentic-facebook/.claude/skills/facebook/SKILL.md` (134 lines, single file) is the same shape for the same job against the same architecture. This skill mirrors its structure deliberately, and diverges from it only where X's behaviour genuinely differs — plus one place where the sibling is wrong (see Design rationale, D2).

## Goals

Let Claude read X/Twitter through the published `agentic-x` CLI and **chain the primitives itself** — the CLI has no `crawl` command by design, so deciding which handle to follow next is the skill's whole reason to exist.

In the user's words: the skill must cover **"해당 패키지를 설치하는 방법"** and **"pypi에서 최신 버전과 로컬에 설치된 버전을 비교해 항상 최신으로 유지"**.

The skill must NOT restate the CLI's flags — `agentic-x catalog` is generated from the parser and is correct for whatever version is actually installed, so a copy in the skill would describe the wrong version the moment the package updates.

## Behavior inventory

| id | behavior/knowledge/constraint | layer | component | status |
|----|-------------------------------|-------|-----------|--------|
| B1 | Install into an isolated env (`uv tool`/`pipx`), never shared `pip` — scrapling pins exact Playwright versions | skill | x | generated |
| B2 | Compare installed version against PyPI at task start; if behind, announce and upgrade before working | skill | x | generated |
| B3 | `catalog` (JSON always) / `schema --json` are the flag and output contracts; never restate them in prose | skill | x | generated |
| B4 | Session check first; `login` needs a human at a real browser; throwaway account only | skill | x | generated |
| B5 | Every read writes a JSON **file**; stdout carries nothing. Always pass `--output` | skill | x | generated |
| B6 | Two output object types — `Tweet` vs `User`. The graph commands emit `User` | skill | x | generated |
| B7 | txid fragility: `search`, `fetch --replies`, `followers` depend on a reverse-engineered header; exit 4 means it rotted | skill | x | generated |
| B8 | The browser fallback returns the **first page only** (`browser_observed`) — never report it as a complete run | skill | x | generated |
| B9 | Likers do not exist on X any more; quoters are `search "quoted_tweet_id:<id>"` | skill | x | generated |
| B10 | `stop_reason` semantics — `empty_pages` means "gave up", not "finished"; exit 7 means `--since` unconfirmed | skill | x | generated |
| B11 | Per-op rate budgets differ sharply; deep chains cost real requests; exit 3 = stop | skill | x | generated |
| B12 | Chaining handles: which field on a result feeds which next command | skill | x | generated |
| B13 | Bound the fan-out before starting it, and report the shape of what was actually done | skill | x | generated |
| B14 | Output is third-party personal data — temp paths, narrow retrieval, delete after | skill | x | generated |
| B15 | A repo checkout and the installed CLI are different versions; the catalog's is the one that counts | skill | x | generated |

## Component specs

**Skill `x`** — `.claude/skills/x/SKILL.md`, single file, no `references/` and no `scripts/`.

- **Single file, deliberately.** Per the split-on-branch rule: an invocation never picks *one of several variants* here — a retrieval task needs the session rules, the output-reading rules and the failure table together. Splitting by length alone would add a routing decision with nothing saved.
- **`description`** must trigger on any "get something off X" phrasing (including a bare x.com URL) and must name two near-misses: developing/testing this package itself is ordinary repo work, and other social networks are out of scope.
- **`allowed-tools`**: `Bash(agentic-x:*)`, `Bash(uv:*)`, `Bash(pipx:*)`, `Bash(curl:*)`, `Read` — the commands the body actually calls, including the version check and the upgrade.
- **Language:** English.
- **Version policy (B2), as decided:** announce the gap in one line, then upgrade automatically and proceed. Not silent (a mid-task version change must be diagnosable) and not gated on a question the user would answer "yes" to every session.

## Design rationale

**D1 — one skill, not several.** Install, session, retrieval, chaining and failure handling all trigger from the same situation ("the user wants something off X") and read as one job. Splitting them would spend the shared description budget several times over for no triggering benefit.

**D2 — check PyPI at task start, contradicting the sibling skill.** `facebook/SKILL.md` argues the opposite ("don't query PyPI at the start of every task; a successful catalog is already the check"). The user challenged this and was right: a successful `catalog` proves the output is correct *for the build you have*, which is a different claim from *the build you have is the right one*. It cannot detect a bug-fix release, a behaviour change, or a new command the skill doesn't yet name — none of those produce an `invalid choice` symptom.

This matters more for this package than it would for most, and that is the actual justification: **agentic-x rots by design.** Query-ids rotate every 2–4 weeks and the transaction-id generator breaks whenever X ships a client build; both fixes arrive only as new releases. Running stale is this package's dominant failure mode, so detecting staleness proactively is worth a round trip. Measured cost of the check: **~40ms**. The sibling skill's reasoning should be revisited on its own next pass.

**D3 — read the version from the simple index, not only the JSON API.** Measured live during this pass: minutes after v0.3.0 was published, `pypi.org/pypi/agentic-twitter/json` still reported `0.2.0` while `pypi.org/simple/agentic-twitter/` already listed `0.3.0`. The JSON endpoint caught up about a minute later. A version check that trusts the JSON API alone can conclude "already latest" immediately after a release that exists.

**D4 — no hooks, no permissions entries, no agents.** Nothing here needs deterministic enforcement: the package already clamps its own non-bypassable rate floor in code, and the destructive-action surface is empty (every command is read-only). Adding a hook would be enforcement theatre over a guarantee the package already makes itself.

**D6 — a package defect found while generating this skill.** `agentic-x catalog` does not accept `--json` (it always emits JSON), while `agentic-x schema` does. Writing the version check against the natural-looking `catalog --json` failed immediately. **Fixed in v0.3.1** rather than documented around: `catalog` now accepts `--json` as a no-op. The skill's note about the asymmetry was removed in the same pass, since keeping a warning about a wart that no longer exists is its own kind of drift.

**D5 — scope note, not a blocker.** The skill lives in this repo's `.claude/`, so it only loads in sessions opened here — same as the fb sibling. Reading X is useful from any project; if the user wants it everywhere, copy the directory to `~/.claude/skills/x/`. Left as the user's call rather than assumed.

## Validation

Structural: **`validate_harness.py` passed 2026-07-20 — 0 errors, 0 warnings.** No hooks were generated, so `test_hook.py` does not apply.

**Live e2e ran 2026-07-20 against the real installed CLI — 4/4 scenarios passed.** Run artifacts were deleted afterwards (they contain scraped third-party data and full transcripts); `.claude/.e2e-runs/` is now gitignored so a future run cannot be committed by accident.

| # | Scenario | Expected | Result |
|---|---|---|---|
| V1 | "What has @nasa been tweeting lately? Just the last 5." | Trigger; detect the stale install; upgrade; retrieve via `--output`; clean up | **Pass** — skill invoked, found 0.2.0 vs PyPI latest, ran `uv tool install --upgrade`, read the file, then deleted it unprompted |
| V2 | "Add a --format csv option to agentic-x's fetch command." | Must NOT trigger — repo work | **Pass** — `skill invocations: []`; did ordinary code work instead |
| V3 | "Find me some recent posts about AI on Instagram." | Must NOT trigger — wrong network | **Pass** — `skill invocations: []`; declined and offered the in-scope alternative |
| V4 | "Who does the @X account follow? Give me the list." | Hit `empty_pages` and report the list as incomplete | **Pass** — refused to present 1 account as the full list, naming `empty_pages` as the reason |

**Headless permission handling is now confirmed working** for this project — `run_e2e.py`'s `claude -p` invocation completed all four scenarios with no auth failure and no permission stall. The caveat in `references/e2e-testing.md` no longer needs re-litigating here.

**One real defect found and fixed (V1).** The version check read the installed version out of `agentic-x catalog`, but `catalog` did not exist before v0.3.0 — so the command crashed with a JSON traceback on exactly the stale installs the step exists to catch. The model recovered on its own, but the skill had handed it a broken command. Now reads `agentic-x --version`, which has existed in every release. Verified directly by downgrading to 0.2.0: the old command raised `JSONDecodeError`, the new one printed `agentic-x 0.2.0`.

**One non-defect worth recording.** V1's upgrade landed on 0.3.0 while PyPI already listed 0.3.1 — the simple index was still propagating, seconds after the release. A retry moments later resolved 0.3.1 correctly, so `uv tool install --upgrade --no-cache` is right; the skill now says to verify the version after upgrading rather than assume it landed.

## Change history

- **2026-07-20 — validated.** Live e2e, 4/4 passed. Found and fixed the version-check crash on stale installs; confirmed headless e2e works in this project.
- **2026-07-20 — new.** First harness pass. Recovered a spec for a repo that had a CLAUDE.md and an empty `.claude/skills/x/`, then specified and generated the `x` retrieval skill. Notable: D2 reverses the sibling skill's version-check stance at the user's challenge.
