#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm_council — run several subscription-auth AI coding CLIs (Claude Code, Codex CLI,
Cursor CLI) on the same prompt in the same project dir, anonymize their answers,
and let a chairman synthesize a joint decision.  No API keys: each CLI uses its own
login (claude login / codex login / agent login).

Usage:
  python llm_council.py -p "question..." [-d C:\\proj] [--members claude,codex,cursor]
                        [--chairman claude] [--context file1 file2 ...]
                        [--prompt-file q.md] [--peer-review] [--write]
                        [--timeout 1200] [--cursor-bin cursor-agent]

Output: council_runs/<timestamp>/ inside the project dir:
  prompt.md, <member>_answer.md, member_map.json, (peer_review_<m>.md), final_decision.md
"""
import argparse, concurrent.futures as cf, datetime, json, os, random, shutil
import subprocess, sys

# Windows konsolu cp1252: Turkce 'i' (ı) basilirken UnicodeEncodeError ile
# COKUYORDU — final_decision.md diske yazildiktan SONRA, yani karar kaybolmuyor
# ama operator hicbir sey gormuyor ve kosu hata vermis gibi bitiyordu (2026-08-05).
for _akis in (sys.stdout, sys.stderr):
    try:
        _akis.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # yeniden yonlendirilmis/kapali akis
        pass

def which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

# ---------------------------------------------------------------- adapters
def run_cli(cmd, cwd, timeout, input_text=None, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    try:
        r = subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           input=input_text, shell=False, env=env,
                           stdin=subprocess.DEVNULL if input_text is None else None)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return -2, "", str(e)

def ask_claude(prompt, cwd, timeout, write):
    exe = which("claude")
    # NOTE: prompt goes over stdin, never argv.  which() resolves to claude.CMD on
    # Windows; cmd.exe truncates any argument at the first newline, which silently
    # ate every multi-line prompt (chairman + peer-review + --context files).
    cmd = [exe, "-p", "--output-format", "json"]
    if not write:
        # --allowedTools ALONE DOES NOT SANDBOX.  It pre-approves those tools; it
        # does NOT deny the rest.  On 2026-08-05 the chairman ran with write=False
        # and still wrote a 634-line doc and `git commit`ed it (OrganikV3 8392251),
        # then cited its own commit back as established authority in the verdict.
        # Denying the mutating tools is what actually makes a review read-only.
        cmd += ["--allowedTools", "Read,Grep,Glob",
                "--disallowedTools",
                "Write,Edit,MultiEdit,NotebookEdit,Bash,Task,WebFetch"]
    else:
        cmd += ["--permission-mode", "acceptEdits"]
    rc, out, err = run_cli(cmd, cwd, timeout, input_text=prompt)
    if rc == 0:
        try:
            return True, json.loads(out).get("result", out)
        except Exception:
            return True, out
    return False, f"[claude rc={rc}] {err[:2000]}\n{out[:2000]}"

def ask_codex(prompt, cwd, timeout, write):
    exe = which("codex")
    sandbox = "workspace-write" if write else "read-only"
    import tempfile
    fd, lastmsg = tempfile.mkstemp(suffix=".txt"); os.close(fd)
    # "-" makes codex read the prompt from stdin (see `codex exec --help`).
    cmd = [exe, "exec", "--sandbox", sandbox, "--skip-git-repo-check",
           "-o", lastmsg, "-"]
    clean_home = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".codex_clean")
    envx = {"CODEX_HOME": clean_home} if os.path.isdir(clean_home) else None
    rc, out, err = run_cli(cmd, cwd, timeout, input_text=prompt, env_extra=envx)
    try:
        text = open(lastmsg, encoding="utf-8", errors="replace").read().strip()
    except Exception:
        text = ""
    finally:
        try: os.remove(lastmsg)
        except OSError: pass
    if rc == 0 and text:
        return True, text
    if rc == 0:
        return True, out
    return False, f"[codex rc={rc}] {err[:2000]}\n{out[:2000]}"

def ask_cursor(prompt, cwd, timeout, write, cursor_bin=None):
    exe = which(cursor_bin) if cursor_bin else which("agent", "cursor-agent")
    if not exe:
        fb = os.path.expandvars(r"%LOCALAPPDATA%\cursor-agent\cursor-agent.cmd")
        exe = fb if os.path.isfile(fb) else exe
    # --trust: non-interactive runs otherwise die with "Workspace Trust Required".
    cmd = [exe, "-p", "--output-format", "text", "--trust"]
    if not write:
        cmd += ["--mode", "ask"]
    else:
        cmd += ["--force"]          # don't block on per-command approval
    rc, out, err = run_cli(cmd, cwd, timeout, input_text=prompt)
    if rc == 0:
        return True, out
    return False, f"[cursor rc={rc}] {err[:2000]}\n{out[:2000]}"

ADAPTERS = {"claude": ask_claude, "codex": ask_codex, "cursor": ask_cursor}
BIN_HINTS = {
    "claude": "npm i -g @anthropic-ai/claude-code  &&  claude login",
    "codex":  "npm i -g @openai/codex  &&  codex login   (ChatGPT hesabiyla)",
    "cursor": "https://cursor.com/cli kurulumu  &&  agent login",
}

def available(member, cursor_bin=None):
    if member == "claude": return which("claude") is not None
    if member == "codex":  return which("codex") is not None
    if member == "cursor":
        if which(cursor_bin) if cursor_bin else which("agent", "cursor-agent"):
            return True
        return os.path.isfile(os.path.expandvars(r"%LOCALAPPDATA%\cursor-agent\cursor-agent.cmd"))
    return False

# ---------------------------------------------------------------- council
CHAIRMAN_TMPL = """You are the CHAIRMAN of an engineering council. {n} anonymous members
(labeled {labels}) independently answered the same question in the same project
directory. Your job is to synthesize a JOINT DECISION.

Structure your answer exactly as:
## Agreements
## Disagreements (and who is right, with reasoning)
## Risks / blind spots none of the members covered
## FINAL JOINT DECISION (actionable, specific)

Do not try to guess which tool produced which answer; judge only content.

AUTHORITY RULE: the {n} answers below are the ONLY council input.  Files that
appeared in the working tree during this run (including anything committed while
the council was running) are DRAFTS, not authority — never cite them as "sealed",
"binding", or already-decided, and never present them as independent confirmation
of your own conclusion.  If a repo file matters, verify its claim against the code
and say so as your own finding.

=== ORIGINAL QUESTION ===
{question}

{answers}
"""

PEER_TMPL = """You are one member of an engineering council. You previously answered the
question below. Now review the OTHER members' anonymized answers. For each, list in
2-4 bullets: what it gets right, what it gets wrong/misses. Then rank all answers
(including your own, labeled SELF) from best to worst with one-line justification.

=== QUESTION ===
{question}

=== YOUR OWN ANSWER (SELF) ===
{own}

{others}
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("-d", "--dir", default=os.getcwd())
    ap.add_argument("--members", default="claude,codex,cursor")
    ap.add_argument("--chairman", default="claude")
    ap.add_argument("--context", nargs="*", default=[],
                    help="files whose content is embedded into the prompt")
    ap.add_argument("--peer-review", action="store_true")
    ap.add_argument("--write", action="store_true",
                    help="allow file modifications (default: read-only analysis)")
    # 1200s yetmiyordu: buyuk depolarda (OrganikV3, 2026-08-05) claude uyesi
    # dosya dogrulamasini bitirmeden timeout'a takildi ve cevabi KAYBOLDU —
    # calismiyor gibi gorundu ama yalnizca yavasti.  Uyeler paralel kostugu
    # icin bu yalniz en yavas uyenin suresini uzatir.
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--cursor-bin")
    a = ap.parse_args()

    question = a.prompt or ""
    if a.prompt_file:
        question = open(a.prompt_file, encoding="utf-8").read()
    if not question:
        ap.error("need -p or --prompt-file")
    for cf_ in a.context:
        question += f"\n\n=== CONTEXT FILE: {os.path.basename(cf_)} ===\n"
        question += open(cf_, encoding="utf-8", errors="replace").read()

    members = [m.strip() for m in a.members.split(",") if m.strip()]
    live, skipped = [], []
    for m in members:
        (live if available(m, a.cursor_bin) else skipped).append(m)
    for m in skipped:
        print(f"[skip] '{m}' CLI bulunamadi.  Kurulum: {BIN_HINTS.get(m,'?')}")
    if not live:
        sys.exit("Hicbir uye CLI'si kurulu degil.")
    if a.chairman not in live:
        print(f"[warn] chairman '{a.chairman}' yok; '{live[0]}' atandi.")
        a.chairman = live[0]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(a.dir, "council_runs", ts)
    os.makedirs(outdir, exist_ok=True)
    open(os.path.join(outdir, "prompt.md"), "w", encoding="utf-8").write(question)
    print(f"Council: uyeler={live} chairman={a.chairman} dir={a.dir}\n-> {outdir}")

    # -- round 1: parallel answers
    answers = {}
    with cf.ThreadPoolExecutor(max_workers=len(live)) as ex:
        futs = {ex.submit(ADAPTERS[m], question, a.dir, a.timeout, a.write,
                          *( [a.cursor_bin] if m == "cursor" else [] )): m
                for m in live}
        for f in cf.as_completed(futs):
            m = futs[f]
            ok, text = f.result()
            answers[m] = (ok, text)
            open(os.path.join(outdir, f"{m}_answer.md"), "w", encoding="utf-8").write(text)
            print(f"[{'ok' if ok else 'FAIL'}] {m} ({len(text)} chars)")

    good = {m: t for m, (ok, t) in answers.items() if ok}
    if not good:
        sys.exit("Tum uyeler basarisiz — ciktilari kontrol et: " + outdir)

    # -- anonymize
    labels = list("ABCDEFG")[:len(good)]
    order = list(good.keys()); random.shuffle(order)
    label_of = {m: labels[i] for i, m in enumerate(order)}
    json.dump({v: k for k, v in label_of.items()},
              open(os.path.join(outdir, "member_map.json"), "w"), indent=1)

    # -- optional round 2: peer review
    reviews = {}
    if a.peer_review and len(good) > 1:
        def peer(m):
            others = "\n".join(f"=== ANSWER {label_of[o]} ===\n{good[o]}"
                               for o in good if o != m)
            pr = PEER_TMPL.format(question=question, own=good[m], others=others)
            return m, ADAPTERS[m](pr, a.dir, a.timeout, False,
                                  *( [a.cursor_bin] if m == "cursor" else [] ))
        with cf.ThreadPoolExecutor(max_workers=len(good)) as ex:
            for m, (ok, txt) in ex.map(peer, list(good)):
                if ok:
                    reviews[m] = txt
                    open(os.path.join(outdir, f"peer_review_{m}.md"), "w",
                         encoding="utf-8").write(txt)
                    print(f"[ok] peer-review {m}")

    # -- chairman synthesis (a 1-member council has nothing to synthesize)
    if len(good) == 1:
        only = next(iter(good))
        dead = ", ".join(m for m in live if m not in good) or "-"
        final = (f"> NOTE: only 1 of {len(live)} members answered ({only}); chairman "
                 f"synthesis skipped.\n> Failed members: {dead}\n\n" + good[only])
        open(os.path.join(outdir, "final_decision.md"), "w", encoding="utf-8").write(final)
        print(f"[warn] tek uye cevapladi ({only}); chairman atlandi. Basarisiz: {dead}")
        print(f"\nTum ciktilar: {outdir}")
        return

    body = "\n".join(f"=== ANSWER {label_of[m]} ===\n{good[m]}" for m in order)
    if reviews:
        body += "\n\n=== PEER REVIEWS (anonymized authors) ===\n"
        body += "\n".join(f"--- review by {label_of[m]} ---\n{t}"
                          for m, t in reviews.items())
    ch_prompt = CHAIRMAN_TMPL.format(n=len(good), labels="/".join(labels),
                                     question=question, answers=body)
    ok, final = ADAPTERS[a.chairman](ch_prompt, a.dir, a.timeout, False,
                                     *( [a.cursor_bin] if a.chairman == "cursor" else [] ))
    open(os.path.join(outdir, "final_decision.md"), "w", encoding="utf-8").write(final)
    print(("\n=== FINAL DECISION (%s) ===\n" % ("ok" if ok else "FAIL")) + final[:3000])
    print(f"\nTum ciktilar: {outdir}")

if __name__ == "__main__":
    main()
