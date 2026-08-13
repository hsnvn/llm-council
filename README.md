# llm_council

**Ask three AI coding assistants the same question. Let a fourth judge their
anonymized answers. Keep the verdict, not the bias.**

`llm_council` is a small command-line tool that runs one prompt through
several AI coding CLIs in parallel — Claude Code, OpenAI Codex CLI and
Cursor CLI — against a repository of your choice, anonymizes their answers
(A/B/C), optionally lets them peer-review each other, and has a designated
*chairman* synthesize a single reasoned verdict.

It was built for real engineering work: design reviews, architecture
decisions, "audit this code against this spec" questions — anywhere a single
model's confident answer deserves adversarial company.

```
┌─────────┐   same prompt    ┌────────────┐
│  claude ├─────────────┐    │            │
├─────────┤             ├───►│ anonymize  ├──► chairman ──► final_decision.md
│  codex  ├─────────────┤    │  (A/B/C)   │    (verdict + reasoning)
├─────────┤             │    │            │
│ cursor  ├─────────────┘    └────────────┘
└─────────┘  read-only, in parallel
```

## Why anonymize?

Models defer to names. When the chairman sees "Claude said X, GPT said Y",
it judges reputations; when it sees "Member A said X", it judges arguments.
The tool strips authorship before synthesis and keeps the mapping in the run
log so *you* can still see who said what.

## Works with your existing subscriptions — no API keys required

Each member runs through its official CLI, and each CLI uses **your own
account login** on your machine:

| Member | CLI | Sign in with |
|---|---|---|
| claude | [Claude Code](https://claude.com/claude-code) | Claude Pro / Max / Team account (`claude` → login) |
| codex  | [Codex CLI](https://github.com/openai/codex) | ChatGPT Plus / Pro account (`codex login`) |
| cursor | [Cursor CLI](https://cursor.com/cli) | Cursor subscription (`cursor-agent login`) |

API keys also work — if a CLI is configured for key-based auth, the tool
neither knows nor cares. But **no key is ever required**: normal account
(subscription) authentication is the primary, tested path.

The tool itself carries **no credentials** and phones nowhere. Everything
runs locally through the CLIs you installed and logged into yourself.

## Install

1. Install the CLIs you want as members (any subset works):

   ```
   npm i -g @anthropic-ai/claude-code
   npm i -g @openai/codex
   # cursor: https://cursor.com/cli, then: cursor-agent login
   ```

2. Clone this repo. Optionally put `council.bat` (Windows) on your PATH,
   e.g. copy it to `%APPDATA%\npm`. It resolves the script relative to its
   own location.

3. **Codex isolated profile (recommended):** the tool runs codex under a
   profile stored next to the script (`.codex_clean/`) so your personal
   codex configuration (plugins, notify hooks) cannot interfere with
   non-interactive runs. Log it in once:

   ```powershell
   $env:CODEX_HOME = "<repo>\.codex_clean"
   codex login
   ```

   If you delete `.codex_clean/`, the tool falls back to your default codex
   profile. Credentials written into `.codex_clean/` are gitignored.

## Usage

```
council -p "Is this retry logic safe under concurrent writers?" -d C:\src\myrepo

council --prompt-file design_question.md -d C:\src\myrepo --peer-review

council -p "..." -d C:\src\myrepo --members claude,codex --chairman claude
```

| Flag | Default | Meaning |
|---|---|---|
| `-p` / `--prompt-file` | — | The question, inline or from a file |
| `-d` | cwd | Repository the members read |
| `--members` | `claude,codex,cursor` | Which CLIs participate |
| `--chairman` | `claude` | Who synthesizes the verdict |
| `--context f1 f2` | — | Extra files injected into every member's prompt |
| `--peer-review` | off | Second round: members critique the anonymized answers before the verdict |
| `--write` | off | Allow members write access (default is read-only) |
| `--timeout` | 1200 s | Per-member time limit |

Output lands in `<repo>/council_runs/<timestamp>/`:

```
council_runs/20260806_102342/
├── claude.md            # each member's raw answer
├── codex.md
├── cursor.md
├── anonymized.md        # what the chairman actually saw
└── final_decision.md    # the verdict
```

## Practical notes

- **Members are read-only by default.** They can inspect the repo (grep,
  read files, run `git show`) but not modify it. `--write` exists for
  implementation rounds; use it deliberately.
- Add `council_runs/` to the target repo's `.git/info/exclude` if that repo
  is visible to people who should not see your process files.
- Big questions take time: three agents exploring a codebase in parallel run
  10–20 minutes. Run it in the background and read `final_decision.md`.
- A member that fails (CLI missing, not logged in, timeout) is reported and
  skipped; the council proceeds with the rest.

## What it has caught in practice

On a production embedded codebase, council runs surfaced — among others — a
unit-scale bug that let a correction term overflow an int16 on the wire, an
out-of-bounds array write triggered only by a specific current-flow pattern,
and a statistically unsupported coefficient table that was replaced by a
7-parameter model validated out-of-sample. Different models miss different
things; the disagreements are where the value lives.

## License

GPL-3.0 — see [LICENSE](LICENSE).
