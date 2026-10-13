#!/usr/bin/env bash
# test-hooks.sh — verify all Brain hooks + nightly scripts actually work, end-to-end.
# Self-contained and SAFE: it installs the hooks into a throwaway temp HOME and
# runs them there, so your real ~/Documents/Brain and ~/.claude are never touched.
# Run it any time after ./install.sh to confirm your install is healthy.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
H="$(mktemp -d -t biab-hooktest)"
export TMPDIR="$H/tmp"             # isolate hook locks (tempfile.gettempdir()) in the throwaway HOME
DAY=$(date +%Y-%m-%d)
SID="biabtest$(date +%s)"          # unique per run → no stale-lock collisions
HB="$H/.claude/hooks/brain"
mkdir -p "$HB" "$H/.claude/logs" "$H/Documents/Brain/Journal" "$H/Documents/Brain/Profile" "$H/.local/bin" "$H/tmp"

# Install the repo's hooks into the temp HOME exactly like install.sh does.
for f in "$REPO"/engine/hooks/*.py; do
  sed "s#__HOME__#$H#g" "$f" > "$HB/$(basename "$f")"
  chmod +x "$HB/$(basename "$f")"
done
sed "s#__HOME__#$H#g" "$REPO/engine/nightly/gbrain-selfupdate.sh" > "$HB/gbrain-selfupdate.sh"
chmod +x "$HB/gbrain-selfupdate.sh"

PASS=0; FAIL=0
ok(){ printf "  \033[1;32m✓\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
no(){ printf "  \033[1;31m✗\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }

# Fake transcript: 1 user prompt + 1 assistant with 3 tool_use (>=3 gate).
TX="$H/fake-transcript.jsonl"
cat > "$TX" <<EOF
{"type":"user","timestamp":"${DAY}T10:00:00Z","message":{"content":[{"type":"text","text":"build a login page with tests"}]}}
{"type":"assistant","timestamp":"${DAY}T10:05:00Z","message":{"model":"claude-opus-4-7","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/proj/a.ts"}},{"type":"tool_use","name":"Edit","input":{"file_path":"/proj/b.ts"}},{"type":"tool_use","name":"Bash","input":{"command":"bun run build"}}]}}
EOF
PAYLOAD="{\"session_id\":\"$SID\",\"cwd\":\"/Users/builder/proj\",\"transcript_path\":\"$TX\"}"

echo "════ 1) correction-detector ════"
verdict() {   # $1 prompt, $2 hook (default: installed one) → CORRECTION | CONFUSION | SILENT
  local out
  out=$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps({"prompt": sys.stdin.read()}))' \
        | HOME="$H" python3 "${2:-$HB/correction-detector.py}" 2>/dev/null)
  case "$out" in "") echo SILENT;; *"CONFUSION DETECTED"*) echo CONFUSION;; *"CORRECTION DETECTED"*) echo CORRECTION;; *) echo "?";; esac
}
expect_all() {   # $1 label, $2 expected verdict, then one prompt per argument
  local label="$1" want="$2" bad="" p; shift 2
  for p in "$@"; do [ "$(verdict "$p")" = "$want" ] || bad="$bad | $p"; done
  local n="$# case"; [ $# -gt 1 ] && n="${n}s"
  [ -z "$bad" ] && ok "$label ($n → $want)" || no "$label — wrong on:$bad"
}
expect_all "real corrections fire" CORRECTION \
  "no, actually do it the other way" \
  "you forgot to update the changelog" \
  "that's not what I asked, I wanted a CLI" \
  "from now on run the tests before pushing" \
  "your fix breaks the login page, it doesn't handle empty input" \
  "use the existing helper instead of a new one" \
  "non, garde l'ancienne version" \
  "t'as oublié de mettre à jour le changelog" \
  "à l'avenir préviens-moi avant de pousser" \
  "c'est pas bon, le total est faux" \
  'no, not that: ```const x = 1``` the other way round'
expect_all "not following the explanation is told apart" CONFUSION \
  "I don't follow, what's a reconciler?" \
  "sorry, I didn't understand the last part" \
  "what do you mean by idempotent here" \
  "j'ai pas tout compris honnêtement" \
  "je vois pas ce que tu veux dire"
expect_all "ordinary negations stay silent" SILENT \
  "build me a dashboard" \
  "the build doesn't work on node 22" \
  "I don't know which library to pick" \
  "why does this not compile?" \
  "I don't understand why the CI fails on main" \
  "no worries, take your time and add tests" \
  "it's not urgent, refactor when you can" \
  "ça marche pas sur mon mac" \
  "je sais pas quelle lib choisir" \
  "tkt c'est pas un pb, on verra demain"
expect_all "relayed content stays silent" SILENT \
  "$(printf '<task-notification>\nAgent "fix login" finished.\nDo NOT retry the deploy, the build is wrong.\n</task-notification>')" \
  "$(printf '[SYSTEM NOTIFICATION - NOT USER INPUT]\nverifier done: no regression, nothing wrong found')" \
  "$(printf 'output of the run\n```\n[run 1] edit refused\n[run 1] npm ci blocked\nnpm ERR! code EACCES\n```')" \
  "$(printf 'worker result\n```\n[E2E] / ......... render OK\n[E2E] /buy ...... render OK\n[E2E] api ....... not reachable\n[E2E] verdict: no regression\n```')"
# A long prompt is a spec or a paste: only its opening can carry a correction.
SPEC="Implement the export feature. $(printf 'Each row carries an id, a label and a total; %.0s' $(seq 12))Use streaming instead of loading everything, and from now on the CSV is the default format."
expect_all "long spec: wording in its body is not a correction" SILENT "$SPEC"
expect_all "long prompt opening on a correction still fires" CORRECTION "No, that's not what I meant. $SPEC"
# The guard fails OPEN: broken or missing, corrections must still fire.
GT="$H/guardtest"; mkdir -p "$GT"; cp "$REPO"/engine/hooks/correction-detector.py "$GT/"
bad=""
for g in 'raise ImportError("broken")' 'def is_relayed(t):
    return 1/0'; do
  printf '%s\n' "$g" > "$GT/_paste_guard.py"
  [ "$(verdict "no, actually the other way" "$GT/correction-detector.py")" = CORRECTION ] || bad="$bad | $g"
done
rm -f "$GT/_paste_guard.py"
[ "$(verdict "no, actually the other way" "$GT/correction-detector.py")" = CORRECTION ] || bad="$bad | missing module"
[ -z "$bad" ] && ok "relay guard broken or missing: corrections still fire" || no "guard failure swallowed corrections:$bad"
rm -rf "$GT"

echo "════ 2) session-logger ════"
echo "$PAYLOAD" | HOME="$H" python3 "$HB/session-logger.py" 2>/dev/null
grep -q "$SID" "$H/.claude/logs/sessions.log" 2>/dev/null && ok "wrote sessions.log" || no "did not write sessions.log"

echo "════ 3) session-indexer ════"
echo "$PAYLOAD" | HOME="$H" python3 "$HB/session-indexer.py" 2>/dev/null
grep -q "$SID" "$H/.claude/logs/sessions-$DAY.jsonl" 2>/dev/null && ok "wrote sessions-$DAY.jsonl" || no "did not write sessions-DAY.jsonl"

echo "════ 4) session-recap ════"
echo "$PAYLOAD" | HOME="$H" python3 "$HB/session-recap.py" 2>/dev/null
J="$H/Documents/Brain/Journal/$DAY.md"
grep -q "📊 Claude Sessions" "$J" 2>/dev/null && ok "Journal section created" || no "no Journal section"
grep -q "sid:.${SID:0:8}" "$J" 2>/dev/null && ok "session entry present" || no "no session entry"
grep -qE "Read 1|Edit 1|Bash 1" "$J" 2>/dev/null && ok "tools recorded" || no "tools not recorded"
grep -q "intent: build a login page" "$J" 2>/dev/null && ok "intent captured" || no "intent not captured"
# A secret pasted in the first prompt must never reach the journal.
FAKEKEY="0x$(printf 'ab%.0s' $(seq 32))"
TX2="$H/fake-transcript-secret.jsonl"
sed "s#build a login page with tests#sign with $FAKEKEY then curl ?user=admin\&password=hunter2-not-real#" "$TX" > "$TX2"
echo "{\"session_id\":\"secretrc$(date +%s)\",\"cwd\":\"/Users/builder/proj\",\"transcript_path\":\"$TX2\"}" | HOME="$H" python3 "$HB/session-recap.py" 2>/dev/null
grep -q "intent: sign with \[REDACTED\]" "$J" 2>/dev/null && ok "secret in intent is redacted" || no "secret not redacted"
! grep -qE "abababab|hunter2" "$J" 2>/dev/null && ok "no secret value left in the journal" || no "secret value leaked into the journal"

echo "════ 5) daily-reflection (claude stubbed) ════"
# claude stub: records its stdin (the prompt) and exits with the code in claude-exit;
# on failure it prints a usage-limit message, which stops the retries at once.
cat > "$H/.local/bin/claude" <<'STUB'
#!/usr/bin/env bash
cat > "$HOME/.claude/logs/claude-stub-prompt.txt"
echo called > "$HOME/.claude/logs/claude-stub-called.txt"
code=$(cat "$HOME/.claude/claude-exit" 2>/dev/null || echo 0)
[ "$code" != 0 ] && echo "You've hit your usage limit"
exit "$code"
STUB
chmod +x "$H/.local/bin/claude"
RL="$H/.claude/logs"
reflect() { HOME="$H" BRAIN_NO_NOTIFY=1 python3 "$HB/daily-reflection.py" "$@" 2>/dev/null; }
stamp() { python3 -c "from datetime import datetime,timedelta; print((datetime.now().astimezone().replace(hour=12,minute=0,second=0,microsecond=0)-timedelta(days=$1)).isoformat())"; }
T0=$(stamp 0); T1=$(stamp 1)
HOME="$H" git config --global user.email builder@example.com
HOME="$H" git config --global user.name Builder
# Sessions often start from a parent folder holding several repos — here a repo
# itself, with an independent repo nested in it (the harder case to discover).
mkdir -p "$H/work/app" "$H/.claude/projects/-work"
git init -q "$H/work"
( cd "$H/work/app" && git init -q && echo a > a && git add a \
  && HOME="$H" git commit -qm "feat: add login page" \
  && echo b > b && git add b && git -c user.email=other@example.com -c user.name=Other commit -qm "chore: someone else" )
FAKEKEY="0x$(printf 'ab%.0s' $(seq 32))"
cat > "$H/.claude/projects/-work/day-session.jsonl" <<EOF
{"type":"user","timestamp":"$T1","cwd":"$H/work","message":{"content":"YESTERDAY-ONLY"}}
{"type":"user","timestamp":"$T0","cwd":"$H/work","message":{"content":"ship the login page, key $FAKEKEY"}}
{"type":"assistant","timestamp":"$T0","cwd":"$H/work","message":{"content":[{"type":"text","text":"On it."},{"type":"tool_use","name":"Bash","input":{"command":"bun test"}}]}}
{"type":"user","timestamp":"$T0","cwd":"$H/work","message":{"content":[{"type":"tool_result","content":"TOOL-OUTPUT-NOISE"}]}}
{"type":"assistant","timestamp":"$T0","cwd":"$H/work","message":{"content":[{"type":"text","text":"Login page shipped."}]}}
EOF
cat > "$H/.claude/projects/-work/own-run.jsonl" <<EOF
{"type":"user","timestamp":"$T0","cwd":"$H/Documents/Brain","message":{"content":"<!-- brain-daily-reflection -->\nSELF-RUN"}}
{"type":"assistant","timestamp":"$T0","message":{"content":[{"type":"text","text":"SELF-RUN a"}]}}
{"type":"assistant","timestamp":"$T0","message":{"content":[{"type":"text","text":"SELF-RUN b"}]}}
EOF
printf -- '---\ntype: memory\n---\n# Memory\n\n## Recent context\n' > "$H/Documents/Brain/Profile/memory.md"
P="$RL/claude-stub-prompt.txt"

out=$(reflect --dry-run)
echo "$out" | grep -q "1 Claude session(s), 0 Hermes session(s), 1 repo(s) with commits, 1 secret(s) masked" && [ ! -f "$RL/claude-stub-called.txt" ] \
  && ok "--dry-run reports the sources without calling claude" || no "--dry-run wrong: $out"
reflect --slot midday
[ -f "$RL/claude-stub-called.txt" ] && grep -q "Journal/$DAY.md" "$P" && grep -q "Login page shipped" "$P" \
  && ok "prompt built from the day's transcript and sent on stdin" || no "claude not called with the day's sessions"
! grep -qE "YESTERDAY-ONLY|TOOL-OUTPUT-NOISE|SELF-RUN" "$P" \
  && ok "other days, tool output and its own past runs are left out" || no "prompt carries out-of-scope text"
grep -q "feat: add login page" "$P" && ! grep -q "someone else" "$P" \
  && ok "git digest: your commits only, nested repo under the session folder found" || no "git digest wrong"
grep -q "\[REDACTED\]" "$P" && ! grep -q "abababab" "$P" \
  && ok "secrets masked before the prompt leaves the machine" || no "secret reached the prompt"

echo 1 > "$H/.claude/claude-exit"
reflect --slot evening >/dev/null
M="$H/Documents/Brain/Profile/memory.md"
grep -q "REFLECTION-STATUS:BEGIN" "$M" && grep -q "usage limit" "$M" && head -1 "$M" | grep -q '^---$' \
  && ok "failure: warning pinned in memory.md, frontmatter intact" || no "failure not surfaced in memory.md"
echo 0 > "$H/.claude/claude-exit"
reflect --day "$DAY" --slot backfill
! grep -q "REFLECTION-STATUS" "$M" && grep -q "BACKFILL run" "$P" \
  && ok "a good run (here a backfill) clears the warning" || no "warning not cleared by a good run"

# A non-UTF-8 locale (cp1252 on Windows; ISO-8859-1 here) must not break the
# hand-off: the prompt carries "→" and accented letters.
rm -f "$TMPDIR"/brain-daily-reflection-*.lock "$RL/claude-stub-called.txt"
LC_ALL=en_US.ISO8859-1 PYTHONUTF8=0 reflect --slot midday >/dev/null
[ -f "$RL/claude-stub-called.txt" ] && grep -q "→ Bash bun test" "$P" \
  && ok "non-UTF-8 locale: prompt still handed over intact" || no "prompt hand-off broke under a non-UTF-8 locale"
rm -f "$RL/claude-stub-called.txt"
reflect --day 2001-01-01 && [ ! -f "$RL/claude-stub-called.txt" ] \
  && ok "exits gracefully when there's nothing to do" || no "crashed or called claude with nothing to do"
LONE="$H/lone"; mkdir -p "$LONE"; cp "$HB/daily-reflection.py" "$HB/session-recap.py" "$LONE/"
HOME="$H" BRAIN_NO_NOTIFY=1 python3 "$LONE/daily-reflection.py" --slot evening 2>/dev/null
[ ! -f "$RL/claude-stub-called.txt" ] && grep -q "_redact.py not found" "$RL/daily-reflection-errors.log" \
  && ok "without _redact.py: no unmasked transcript is ever sent" || no "ran without its redaction module"
rm -rf "$M" "$LONE" "$H/work" "$H/.claude/projects" "$H/.gitconfig"   # leave the shared HOME as section 6 expects it

echo "════ 6) gbrain-selfupdate (pull → install → migrate → smoke-test → rollback) ════"
mkdir -p "$H/.gbrain" "$H/DEV" "$H/Documents/Brain/Profile" "$H/.bun/bin"

# Fake "gbrain" repo + its bare remote, so `git pull --ff-only` inside the
# script behaves exactly like the real ~/DEV/gbrain clone.
REMOTE="$H/DEV/gbrain-remote.git"
GREPO="$H/DEV/gbrain"
git init -q --bare "$REMOTE"
git clone -q "$REMOTE" "$GREPO" 2>/dev/null
git clone -q "$REMOTE" "$H/DEV/gbrain-src" 2>/dev/null
(
  cd "$H/DEV/gbrain-src" \
    && printf '# Changelog\n\n- feat: first release\n' > CHANGELOG.md \
    && git add -A && git -c user.email=t@t -c user.name=t commit -qm "chore: changelog" \
    && git push -q origin HEAD
)
( cd "$GREPO" && git pull -q --ff-only )   # bring the clone up to the same commit as a real install would be

# Stub bun/gbrain so the script never touches real binaries.
# bun: `--version` reads a file (1.3.12 by default), `upgrade` bumps it to 1.4.2
# unless told to fail.
cat > "$H/.bun/bin/bun" <<STUBEOF
#!/usr/bin/env bash
case "\$1" in
  --version) cat "$H/.gbrain/bun-version" 2>/dev/null || echo 1.3.12;;
  upgrade)   [ -f "$H/.gbrain/bun-upgrade-fails" ] && exit 1; echo 1.4.2 > "$H/.gbrain/bun-version";;
esac
exit 0
STUBEOF
chmod +x "$H/.bun/bin/bun"
echo 0 > "$H/.gbrain/doctor-exit"
cat > "$H/.bun/bin/gbrain" <<STUBEOF
#!/usr/bin/env bash
case "\$1" in
  doctor) exit "\$(cat "$H/.gbrain/doctor-exit" 2>/dev/null || echo 0)";;
esac
exit 0
STUBEOF
chmod +x "$H/.bun/bin/gbrain"

SU_LOG="$H/.gbrain/su-test.log"
MEM="$H/Documents/Brain/Profile/memory.md"

HOME="$H" "$HB/gbrain-selfupdate.sh" "$SU_LOG" 2>/dev/null
[ ! -f "$MEM" ] && ok "no-op when already up to date (no memory.md write)" || no "wrote memory.md with nothing new to report"

( cd "$H/DEV/gbrain-src" && printf -- '- feat: new thing\n' >> CHANGELOG.md \
    && git -c user.email=t@t -c user.name=t commit -aqm "feat: new thing" && git push -q origin HEAD )
HOME="$H" "$HB/gbrain-selfupdate.sh" "$SU_LOG" 2>/dev/null
grep -q "GBRAIN-UPDATE:BEGIN" "$MEM" 2>/dev/null && ok "memory.md gets a block on a real version change" || no "memory.md not updated on new version"
grep -q "feat: new thing" "$MEM" 2>/dev/null && ok "changelog line surfaced in the block" || no "changelog not surfaced"
python3 -c "import json,sys; d=json.load(open('$H/.gbrain/last-update.json')); sys.exit(0 if d['status']=='updated' else 1)" \
  && ok "last-update.json status=updated" || no "wrong status in last-update.json"

before_rb=$(cd "$GREPO" && git rev-parse --short HEAD)
( cd "$H/DEV/gbrain-src" && printf -- '- feat: bad release\n' >> CHANGELOG.md \
    && git -c user.email=t@t -c user.name=t commit -aqm "feat: bad release" && git push -q origin HEAD )
echo 1 > "$H/.gbrain/doctor-exit"   # next "gbrain doctor" (the smoke test) fails
HOME="$H" "$HB/gbrain-selfupdate.sh" "$SU_LOG" 2>/dev/null
after_rb=$(cd "$GREPO" && git rev-parse --short HEAD)
[ "$before_rb" = "$after_rb" ] && ok "auto-rollback: HEAD reverted after a failed smoke test" || no "did not roll back — HEAD still moved"
python3 -c "import json,sys; d=json.load(open('$H/.gbrain/last-update.json')); sys.exit(0 if d['status']=='rolled_back' else 1)" \
  && ok "last-update.json status=rolled_back" || no "wrong status after rollback"
grep -q "rolled back" "$MEM" 2>/dev/null && ok "rollback surfaced in memory.md" || no "rollback not surfaced"
echo 0 > "$H/.gbrain/doctor-exit"

# gbrain starts requiring a newer Bun than the installed one (engines.bun).
( cd "$H/DEV/gbrain-src" && printf '{"engines":{"bun":">=1.4.0"}}\n' > package.json \
    && git add -A && git -c user.email=t@t -c user.name=t commit -qm "chore: require bun 1.4" && git push -q origin HEAD )
HOME="$H" "$HB/gbrain-selfupdate.sh" "$SU_LOG" 2>/dev/null
[ "$(cat "$H/.gbrain/bun-version" 2>/dev/null)" = "1.4.2" ] && grep -q "needs Bun >= 1.4.0" "$SU_LOG" \
  && ok "Bun upgraded first when gbrain requires a newer one" || no "Bun not upgraded before installing gbrain"
python3 -c "import json,sys; d=json.load(open('$H/.gbrain/last-update.json')); sys.exit(0 if d['status']=='updated' else 1)" \
  && grep -q "Bun upgraded to 1.4.2" "$MEM" && [ ! -f "$H/.gbrain/bun.pre-upgrade" ] \
  && ok "update kept, Bun upgrade surfaced in memory.md" || no "update with Bun upgrade not reported"

# bun upgrade cannot reach the minimum: gbrain won't start → rollback with the real cause, old Bun restored.
rm -f "$H/.gbrain/bun-version"; touch "$H/.gbrain/bun-upgrade-fails"; echo 1 > "$H/.gbrain/doctor-exit"
( cd "$H/DEV/gbrain-src" && printf '{"engines":{"bun":">=1.9.0"}}\n' > package.json \
    && git -c user.email=t@t -c user.name=t commit -aqm "chore: require bun 1.9" && git push -q origin HEAD )
before_bun=$(cd "$GREPO" && git rev-parse --short HEAD)
HOME="$H" "$HB/gbrain-selfupdate.sh" "$SU_LOG" 2>/dev/null
[ "$(cd "$GREPO" && git rev-parse --short HEAD)" = "$before_bun" ] && grep -q "Bun restored" "$SU_LOG" \
  && grep -q "needs Bun >= 1.9.0 and \`bun upgrade\` could not get there" "$MEM" \
  && ok "Bun upgrade impossible: rolled back, Bun restored, cause in memory.md" || no "Bun shortfall not handled on rollback"
rm -f "$H/.gbrain/bun-upgrade-fails"; echo 0 > "$H/.gbrain/doctor-exit"

echo "════ 7) gbrain-update-check — SessionStart daily catch-up ════"
rm -f "$H/.gbrain/last-selfupdate-check"
MARKER="$H/.gbrain/selfupdate-ran.marker"
rm -f "$MARKER"
cat > "$HB/gbrain-selfupdate.sh" <<STUB
#!/usr/bin/env bash
echo ran >> "$MARKER"
STUB
chmod +x "$HB/gbrain-selfupdate.sh"

echo '{"session_id":"s1"}' | HOME="$H" python3 "$HB/gbrain-update-check.py" 2>/dev/null
for i in 1 2 3 4 5 6 7 8 9 10; do [ -f "$MARKER" ] && break; sleep 0.2; done
[ -f "$MARKER" ] && ok "dispatches self-update on the first session of the day" || no "did not dispatch on first session"
[ "$(cat "$H/.gbrain/last-selfupdate-check" 2>/dev/null)" = "$DAY" ] && ok "stamps today's date" || no "stamp missing or wrong"

rm -f "$MARKER"
echo '{"session_id":"s2"}' | HOME="$H" python3 "$HB/gbrain-update-check.py" 2>/dev/null
sleep 0.5
[ ! -f "$MARKER" ] && ok "second session same day: rate-limited, no re-dispatch" || no "re-dispatched within the same day"

echo "════ 8) vault-skeleton — YAML frontmatters parse cleanly ════"
# Catches unquoted {{PLACEHOLDER}} that breaks Claude Code / gstack skill loaders.
# Uses ruby (preinstalled on macOS, no extra dep needed).
if command -v ruby >/dev/null 2>&1; then
  while IFS= read -r f; do
    head -1 "$f" | grep -q '^---$' || continue
    rel="${f#"$REPO"/vault-skeleton/}"
    fm=$(awk '/^---$/{n++; next} n==1' "$f")
    err=$(printf '%s' "$fm" | ruby -ryaml -e 'YAML.safe_load(STDIN.read)' 2>&1)
    [ -z "$err" ] && ok "$rel" || no "$rel — $(printf '%s' "$err" | head -1)"
  done < <(find "$REPO/vault-skeleton" -name "*.md" -type f)
else
  printf "  \033[1;33m⚠\033[0m skipped (no ruby found) — install ruby to enable YAML validation\n"
fi

echo "════ 9) gbrain-nightly — waits for the network, retries the vault push ════"
# Runs the real nightly in its own throwaway HOME, with gbrain and the self-update
# stubbed. python3 (the DNS probe) and sleep are shadowed through ~/.bun/bin, which
# comes first on the nightly's PATH: a dead resolver is faked without waiting 5 min,
# and no real DNS is needed.
nightly_run() {   # $1 = probe exit code (0 = network up), $2 = vault remote ("" = a local bare repo)
  NH="$(mktemp -d -t biab-nightly)"
  mkdir -p "$NH/.bun/bin" "$NH/.gbrain" "$NH/nightly" "$NH/Documents/Brain"
  printf '#!/bin/sh\nexit 0\n' > "$NH/.bun/bin/gbrain"
  printf '#!/bin/sh\nexit 0\n' > "$NH/.bun/bin/sleep"
  printf '#!/bin/sh\nexit %s\n' "$1" > "$NH/.bun/bin/python3"
  printf '#!/bin/sh\nexit 0\n' > "$NH/nightly/gbrain-selfupdate.sh"
  cp "$REPO/engine/nightly/gbrain-nightly.sh" "$NH/nightly/"
  chmod +x "$NH/.bun/bin/"* "$NH/nightly/"*.sh
  git init -q --bare "$NH/remote.git"
  git -C "$NH/Documents/Brain" init -q
  git -C "$NH/Documents/Brain" remote add origin "${2:-$NH/remote.git}"
  echo note > "$NH/Documents/Brain/a.md"
  HOME="$NH" zsh "$NH/nightly/gbrain-nightly.sh" 2>/dev/null
  NLOG="$NH/.gbrain/nightly.log"
}
nightly_run 0 ""
grep -q "vault pushed to origin (attempt 1)" "$NLOG" && git --git-dir="$NH/remote.git" rev-parse -q --verify HEAD >/dev/null \
  && ok "network up: vault committed and pushed" || no "network up: vault not pushed"
rm -rf "$NH"
nightly_run 1 ""
grep -q "still unresolvable" "$NLOG" && grep -q "push SKIPPED — no network" "$NLOG" \
  && ok "no network: waits, then says why the push was skipped" || no "no network: cause not logged"
rm -rf "$NH"
nightly_run 0 "/nonexistent/remote.git"
[ "$(grep -c '^fatal:' "$NLOG")" -ge 3 ] && grep -q "push FAILED after 3 attempts" "$NLOG" \
  && ok "push error: retried 3 times, then reported" || no "push error: not retried or not reported"
rm -rf "$NH"

echo "════ 10) gbq + brain_search ════"
# gbq against a stub gbrain: sync exits with $GBQ_STUB_RC; query prints a hit, then
# either exits with $GBQ_STUB_RC or hangs like the real PGLite read does.
GQ="$H/gbq-home"; mkdir -p "$GQ/.bun/bin" "$GQ/.gbrain/brain.pglite"
cat > "$GQ/.bun/bin/gbrain" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  sync)  exit "${GBQ_STUB_RC:-0}";;
  query) echo "hit: pricing.md"; [ -n "$GBQ_STUB_RC" ] && exit "$GBQ_STUB_RC"; exec sleep 60;;
esac
STUB
chmod +x "$GQ/.bun/bin/gbrain"
HOME="$GQ" GBQ_STUB_RC=3 zsh "$REPO/engine/bin/gbq" sync >/dev/null 2>&1; rc=$?
[ "$rc" = 3 ] && ok "gbq: a write returns gbrain's exit code" || no "gbq: write returned $rc, want 3"
t0=$(date +%s); out=$(HOME="$GQ" zsh "$REPO/engine/bin/gbq" query pricing 2>&1); rc=$?; took=$(( $(date +%s) - t0 ))
[ "$rc" = 0 ] && echo "$out" | grep -q "hit: pricing.md" && [ "$took" -lt 15 ] \
  && ok "gbq: a read that never exits is cut once its output settles (${took}s)" || no "gbq: hung read not handled (rc=$rc, ${took}s)"
HOME="$GQ" GBQ_STUB_RC=2 zsh "$REPO/engine/bin/gbq" query pricing >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "gbq: a read that fails on its own reports the failure" || no "gbq: failing read returned $rc, want 2"

BV="$H/bs-vault"; BSI="$H/bs-index.json"; BS="$REPO/engine/search/brain_search.py"
mkdir -p "$BV/Decisions"
printf '# Pricing\n\nWe chose the annual plan → cheaper for small teams.\n' > "$BV/Decisions/pricing.md"
printf '# Lunch\n\nNotes about the cafeteria menu.\n' > "$BV/lunch.md"
BRAIN_SEARCH_INDEX="$BSI" python3 "$BS" index --brain "$BV" >/dev/null 2>&1
out=$(BRAIN_SEARCH_INDEX="$BSI" python3 "$BS" query "annual plan" --json 2>/dev/null)
python3 -c 'import json,sys; h=json.loads(sys.argv[1])["hits"]; sys.exit(0 if h and h[0]["path"]=="Decisions/pricing.md" else 1)' "$out" 2>/dev/null \
  && ok "brain_search: indexes the vault and ranks the right note first" || no "brain_search: wrong or no ranking"
BRAIN_SEARCH_INDEX="$BSI" PYTHONIOENCODING=ascii python3 "$BS" query "annual plan" >/dev/null 2>&1 \
  && ok "brain_search: a non-UTF-8 console does not crash on '→'" || no "brain_search: crashed on a non-UTF-8 console"

echo "════ 11) skills → ~/.claude/skills ════"
SK="$H/skills-test"; SSRC="$SK/src"; SDST="$SK/dst"; SMF="$SK/skills.json"
mkdir -p "$SSRC/alpha" "$SSRC/beta" "$SSRC/gamma" "$SDST/beta"
for s in alpha beta gamma; do printf -- '---\nname: %s\ndescription: test\n---\nv1\n' "$s" > "$SSRC/$s/SKILL.md"; done
printf 'my own beta\n' > "$SDST/beta/SKILL.md"
sk(){ python3 "$REPO/engine/install_skills.py" --src "$SSRC" --dest "$SDST" --manifest "$SMF"; }
sk >/dev/null
cmp -s "$SSRC/alpha/SKILL.md" "$SDST/alpha/SKILL.md" && ok "skills: a missing skill is installed" || no "skills: alpha not installed"
grep -q 'my own beta' "$SDST/beta/SKILL.md" && ok "skills: a same-name skill the user already had is left alone" || no "skills: user's beta overwritten"
sk | grep -q 'up to date alpha' && ok "skills: a second run changes nothing" || no "skills: second run not idempotent"
echo v2 >> "$SSRC/alpha/SKILL.md"; touch "$SDST/gamma/.DS_Store"; echo v2 >> "$SSRC/gamma/SKILL.md"; sk >/dev/null
grep -q v2 "$SDST/alpha/SKILL.md" && grep -q v2 "$SDST/gamma/SKILL.md" \
  && ok "skills: an untouched skill is updated (OS junk files ignored)" || no "skills: untouched skill not updated"
echo 'my note' >> "$SDST/alpha/SKILL.md"; echo v3 >> "$SSRC/alpha/SKILL.md"; out=$(sk)
grep -q 'my note' "$SDST/alpha/SKILL.md" && ! grep -q v3 "$SDST/alpha/SKILL.md" && echo "$out" | grep -q 'you edited' \
  && ok "skills: a skill the user edited is kept and reported" || no "skills: user's edit lost or not reported"
if [ -d "$REPO/skills" ]; then
  python3 - "$REPO/skills" <<'PY' && ok "skills: every bundled skill has a name matching its folder and a description" || no "skills: bad SKILL.md frontmatter (see above)"
import re, sys
from pathlib import Path
bad = []
for d in sorted(p for p in Path(sys.argv[1]).iterdir() if p.is_dir()):
    text = (d / "SKILL.md").read_text(encoding="utf-8") if (d / "SKILL.md").is_file() else ""
    fm = re.match(r"---\n(.*?)\n---\n", text, re.S)
    name = re.search(r"^name:\s*(\S+)\s*$", fm.group(1), re.M) if fm else None
    if not (fm and name and name.group(1) == d.name and re.search(r"^description:\s*\S", fm.group(1), re.M)):
        bad.append(d.name)
print("  bad: %s" % ", ".join(bad)) if bad else None
sys.exit(1 if bad else 0)
PY
  ! grep -rqi --exclude=LICENSE 'hermes' "$REPO/skills" && ok "skills: no Hermes leftovers in the bundled skills (attributions aside)" || no "skills: Hermes references remain"
fi

echo "════ 12) install.sh keeps what it replaces ════"
PH="$H/place-test"; mkdir -p "$PH/src" "$PH/dst"
eval "$(sed -n '/^# >>> place$/,/^# <<< place$/p' "$REPO/install.sh")"
# place() as install.sh runs it; prints the number of backups, then the files kept.
# $PH/repo stands for the repo's git history; a source outside it has none (a ZIP).
pl(){ ( H="$PH" U=tester REPO="$PH/repo" BACKUP="${PB:-$PH/backup}" BACKED_UP=0 MANIFEST="$PH/installed.json" PENDING="$PH/en-attente" KEPT=""
        ok(){ :; }; warn(){ :; }; place "$@"; echo "$BACKED_UP$KEPT" ); }
mtime(){ python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_mtime_ns)' "$1"; }
mode(){ python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$1"; }
recorded(){ python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$PH/installed.json" "$1" 2>/dev/null; }
printf 'v1 __HOME__\n' > "$PH/src/hook.py"
n=$(pl "$PH/src/hook.py" "$PH/dst/hook.py")
[ "$n" = 0 ] && grep -q "v1 $PH" "$PH/dst/hook.py" && [ -x "$PH/dst/hook.py" ] && [ "$(recorded "$PH/dst/hook.py")" = "$(sha256 "$PH/dst/hook.py")" ] \
  && ok "install: a new file is placed (__HOME__ filled in, executable) and its sha256 recorded" || no "install: new file not placed or not recorded"
cp "$PH/installed.json" "$PH/m1"; t1=$(mtime "$PH/dst/hook.py")
n=$(pl "$PH/src/hook.py" "$PH/dst/hook.py")
[ "$n" = 0 ] && [ ! -d "$PH/backup" ] && [ ! -d "$PH/en-attente" ] && cmp -s "$PH/m1" "$PH/installed.json" && [ "$(mtime "$PH/dst/hook.py")" = "$t1" ] \
  && ok "install: a re-run with nothing new changes nothing" || no "install: a re-run with nothing new touched something"
printf 'v2 __HOME__\n' > "$PH/src/hook.py"
n=$(pl "$PH/src/hook.py" "$PH/dst/hook.py")
[ "$n" = 0 ] && grep -q "v2 $PH" "$PH/dst/hook.py" && [ ! -d "$PH/backup" ] && [ "$(recorded "$PH/dst/hook.py")" = "$(sha256 "$PH/dst/hook.py")" ] \
  && ok "install: a file nobody changed is updated, no backup needed" || no "install: untouched file not updated"
v2=$(recorded "$PH/dst/hook.py")
printf 'my own tweak\n' > "$PH/dst/hook.py"; printf 'v3 __HOME__\n' > "$PH/src/hook.py"
n=$(pl "$PH/src/hook.py" "$PH/dst/hook.py")
[ "$n" = "0 hook.py" ] && grep -q 'my own tweak' "$PH/dst/hook.py" && grep -q "v3 $PH" "$PH/en-attente/hook.py" && [ ! -d "$PH/backup" ] \
  && [ "$(recorded "$PH/dst/hook.py")" = "$v2" ] \
  && ok "install: a file changed after install is kept, the new version waits in en-attente/" || no "install: user's change overwritten or not reported ($n)"
printf 'from an older install\n' > "$PH/dst/gbq"; printf 'gbq v1\n' > "$PH/src/gbq"
n=$(pl "$PH/src/gbq" "$PH/dst/gbq")
[ "$n" = 1 ] && grep -q 'older install' "$PH/backup/gbq" && grep -q 'gbq v1' "$PH/dst/gbq" && [ "$(recorded "$PH/dst/gbq")" = "$(sha256 "$PH/dst/gbq")" ] \
  && ok "install: a file not in the manifest, no git history to judge it: backed up, then replaced" || no "install: pre-manifest file not backed up ($n)"
printf 'not json {' > "$PH/installed.json"; printf 'my gbq\n' > "$PH/dst/gbq"; printf 'gbq v2\n' > "$PH/src/gbq"
n=$(pl "$PH/src/gbq" "$PH/dst/gbq" 2>&1)
[ "$n" = 1 ] && grep -q 'my gbq' "$PH/backup/gbq" && grep -q 'gbq v2' "$PH/dst/gbq" && [ "$(recorded "$PH/dst/gbq")" = "$(sha256 "$PH/dst/gbq")" ] \
  && ok "install: an unreadable manifest counts as none (backup, replace, rewritten)" || no "install: unreadable manifest mishandled ($n)"
printf '{"%s": "not a sha256"}' "$PH/dst/gbq" > "$PH/installed.json"; printf 'my gbq again\n' > "$PH/dst/gbq"; printf 'gbq v3\n' > "$PH/src/gbq"
n=$(pl "$PH/src/gbq" "$PH/dst/gbq")
[ "$n" = 1 ] && grep -q 'my gbq again' "$PH/backup/gbq" && grep -q 'gbq v3' "$PH/dst/gbq" \
  && ok "install: a damaged manifest entry counts as none, the file is not frozen for good" || no "install: damaged manifest entry mishandled ($n)"
# The repo's history, as a clone has it: h.py went v1 → deleted → v2, and v3 is about to be installed.
PR="$PH/repo"; mkdir -p "$PR/engine"
g(){ git -C "$PR" -c user.name=t -c user.email=t@t -c commit.gpgsign=false -c core.hooksPath=/dev/null "$@" >/dev/null 2>&1; }
g init -q; printf 'v1 __HOME__ __USER__\n' > "$PR/engine/h.py"; g add -A; g commit -qm 1
g rm -q engine/h.py; g commit -qm 2
mkdir -p "$PR/engine"; printf 'v2 __HOME__\n' > "$PR/engine/h.py"; g add -A; g commit -qm 3
printf 'v3 __HOME__\n' > "$PR/engine/h.py"
mkdir -p "$PH/old/a" "$PH/old/b" "$PH/old/c"; rm -rf "$PH/backup" "$PH/en-attente"
printf 'v1 %s tester\n' "$PH" > "$PH/old/a/h.py"
n=$(pl "$PR/engine/h.py" "$PH/old/a/h.py")
[ "$n" = 0 ] && grep -q "v3 $PH" "$PH/old/a/h.py" && [ ! -d "$PH/backup" ] && [ ! -d "$PH/en-attente" ] \
  && ok "install: an untouched file from before the manifest (a version git knows) is updated, no backup" || no "install: old untouched file mishandled ($n)"
printf 'v2 %s/Documents/Cerveau\n' "$PH" > "$PH/old/b/h.py"
n=$(pl "$PR/engine/h.py" "$PH/old/b/h.py")
[ "$n" = "0 h.py" ] && grep -q Cerveau "$PH/old/b/h.py" && grep -q "v3 $PH" "$PH/en-attente/h.py" && [ ! -d "$PH/backup" ] \
  && ok "install: a file adapted before the manifest existed (matches no version in git) is kept" || no "install: adapted pre-manifest file replaced ($n)"
: > "$PH/old/c/h.py"
n=$(pl "$PR/engine/h.py" "$PH/old/c/h.py")
[ "$n" = "0 h.py" ] && [ ! -s "$PH/old/c/h.py" ] \
  && ok "install: an emptied file is kept (the commit that deleted the source is no version)" || no "install: emptied file replaced ($n)"
mkdir -p "$PH/dotfiles"; printf 'mine\n' > "$PH/dotfiles/s.py"; ln -s ../dotfiles/s.py "$PH/dst/s.py"; printf 's1\n' > "$PH/src/s.py"
n=$(pl "$PH/src/s.py" "$PH/dst/s.py")
[ "$n" = "0 s.py" ] && [ -L "$PH/dst/s.py" ] && grep -qx mine "$PH/dotfiles/s.py" && grep -qx s1 "$PH/en-attente/s.py" \
  && ok "install: a symlinked file stays a link to the user's file, the new version waits" || no "install: symlink replaced ($n)"
printf 'm1\n' > "$PH/src/m.py"; printf 'my m\n' > "$PH/dst/m.py"
if [ "$(id -u)" != 0 ]; then
  mkdir -p "$PH/ro"; chmod 500 "$PH/ro"
  n=$(PB="$PH/ro/backup" pl "$PH/src/m.py" "$PH/dst/m.py" 2>/dev/null); chmod 700 "$PH/ro"
  [ "$n" = "0 m.py" ] && grep -qx 'my m' "$PH/dst/m.py" \
    && ok "install: no backup possible, no replacement" || no "install: replaced without a backup ($n)"
fi
rm -f "$PH/dst/m.py" "$PH/modes"
n=$(mv(){ command mv "$@" && ls -l "${@: -1}" | cut -c1-10 >> "$PH/modes"; }; pl "$PH/src/m.py" "$PH/dst/m.py")
[ "$(cat "$PH/modes")" = "-rwxr-xr-x" ] \
  && ok "install: a script lands already executable (no window where a hook cannot run)" || no "install: script landed as $(cat "$PH/modes")"
printf '<string>com.__USER__.x</string> __HOME__\n' > "$PH/src/x.plist.template"
n=$(pl "$PH/src/x.plist.template" "$PH/dst/com.tester.x.plist")
[ "$n" = 0 ] && grep -q "com.tester.x</string> $PH" "$PH/dst/com.tester.x.plist" && [ "$(mode "$PH/dst/com.tester.x.plist")" = 644 ] \
  && ok "install: a launchd plist gets __USER__ and __HOME__, mode 644" || no "install: plist placed wrong ($n)"

echo "════ 13) install.sh and ~/.claude/settings.json ════"
SH="$H/settings-test"; SJ="$SH/.claude/settings.json"; mkdir -p "$SH/.claude"
eval "$(sed -n '/^# >>> settings$/,/^# <<< settings$/p' "$REPO/install.sh")"
ms(){ ( ok(){ printf '%s\n' "$*"; }; die(){ printf 'DIED %s\n' "$*"; exit 1; }; merge_settings "$SH" "$@" ); }
baks(){ set -- "$SH/.claude"/settings.json.bak-*; [ -e "$1" ] && echo $# || echo 0; }
printf '{\n  "model": "opus",\n  "hooks": {,}\n}\n' > "$SJ"; cp "$SJ" "$SH/ref"
out=$(ms --check); rc1=$?; out2=$(ms); rc2=$?
[ "$rc1" = 1 ] && [ "$rc2" = 1 ] && cmp -s "$SH/ref" "$SJ" && [ "$(baks)" = 0 ] && echo "$out2" | grep -q "DIED $SJ is not valid JSON (line 3, column" \
  && ok "settings: one that does not parse is left untouched, the install stops on file + line" || no "settings: broken file touched or not reported: $out2"
printf '{"hooks": {"Stop": [{"hooks": ["/x/not-an-object.sh"]}]}}\n' > "$SJ"; cp "$SJ" "$SH/ref"
out=$(ms --check); rc=$?
[ "$rc" = 1 ] && cmp -s "$SH/ref" "$SJ" && echo "$out" | grep -q '"Stop" that is not a list of hook groups' \
  && ok "settings: a malformed hooks list stops the install at preflight, not half-way through" || no "settings: malformed hooks list passed the preflight: $out"
printf '\357\273\277{"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x/mine.sh"}]}]}}\n' > "$SJ"
chmod 644 "$SJ"; cp "$SJ" "$SH/ref"
out=$(ms); rc=$?; bak=$(ls "$SH/.claude"/settings.json.bak-* 2>/dev/null)
[ "$rc" = 0 ] && [ "$(baks)" = 1 ] && [ "$(mode "$bak")" = 600 ] && cmp -s "$SH/ref" "$bak" && [ "$(mode "$SJ")" = 644 ] \
  && [ -z "$(find "$SH/.claude" -maxdepth 1 -name '.settings*')" ] && python3 - "$SJ" "$SH" <<'PY' && ok "settings: merged (BOM read), previous version kept as .bak in 0600, written atomically" || no "settings: merge or backup wrong: $out"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
cmds = lambda e: [h["command"] for g in d["hooks"][e] for h in g["hooks"]]
b = sys.argv[2] + "/.claude/hooks/brain/"
assert d["model"] == "opus"
assert cmds("Stop") == ["/x/mine.sh", b + "session-logger.py", b + "session-indexer.py", b + "session-recap.py"]
assert cmds("UserPromptSubmit") == [b + "correction-detector.py"] and cmds("SessionStart") == [b + "gbrain-update-check.py"]
PY
cp "$SJ" "$SH/ref"; out=$(ms)
[ "$(baks)" = 1 ] && cmp -s "$SH/ref" "$SJ" && echo "$out" | grep -q "already registered" \
  && ok "settings: a re-run with the hooks already there writes nothing" || no "settings: needless rewrite or backup"
rm -f "$SJ" "$SH/.claude"/settings.json.bak-*; mkdir -p "$SH/dotfiles"; printf '{}\n' > "$SH/dotfiles/settings.json"
ln -s ../dotfiles/settings.json "$SJ"; ms >/dev/null
[ -L "$SJ" ] && grep -q correction-detector "$SH/dotfiles/settings.json" \
  && ok "settings: a symlinked settings.json stays a symlink" || no "settings: symlink replaced by a file"

echo "════ 14) install.sh logs what its tools say, never the Voyage key ════"
LOG="$H/install-test.log"; VOYAGE="pa-test-$(date +%s)"; VOYAGE_STORED="pa-stored-key"
eval "$(sed -n '/^# >>> log$/,/^# <<< log$/p' "$REPO/install.sh")"
logged sh -c "echo cloning; echo token $VOYAGE >&2; echo old pa-stored-key; exit 4"; rc=$?
[ "$rc" = 4 ] && grep -q '^\[.*\] \$ sh -c' "$LOG" && grep -q "cloning" "$LOG" && grep -q "token \[voyage key\]" "$LOG" \
  && ! grep -qE "$VOYAGE|pa-stored-key" "$LOG" && grep -q '\] exit 4$' "$LOG" \
  && ok "log: output and exit code kept with a timestamp, the Voyage key masked" || no "log: wrong content or key leaked"
out=$(warn(){ echo "W $*"; }; warn_log "bun failed")
echo "$out" | grep -q "W bun failed" && echo "$out" | grep -q "│ .*cloning" && [ "$(echo "$out" | grep -c '│')" -le 15 ] \
  && ok "log: a failure shows the last lines of the log" || no "log: tail not shown on failure"
out=$( (set -euo pipefail; unset VOYAGE VOYAGE_STORED; logged sh -c 'echo hi; exit 3' || echo "caught $?") 2>&1 )
[ "$out" = "caught 3" ] && ok "log: under set -euo pipefail, with no key known, a failing tool is caught by ||" || no "log: logged broke under install.sh's shell options: $out"
grep -n 'VOYAGE' "$REPO/install.sh" | grep -v 'env -u VOYAGE_API_KEY' | grep -E 'logged|"\$GBQ_BIN"|git clone|bun (install|link)' \
  && no "log: the Voyage key is passed to a logged command" || ok "log: no logged command receives the Voyage key"
grep -v '^[[:space:]]*#' "$REPO/install.sh" | grep -E 'bun (install|link)|bun\.sh/install|\./setup|brew install' | grep -v 'env -u VOYAGE_API_KEY' \
  && no "log: code that is not gbrain inherits VOYAGE_API_KEY" || ok "log: bun, gstack's setup and brew run without VOYAGE_API_KEY"

echo ""
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32m✅ All hooks healthy: %s/%s passed.\033[0m\n" "$PASS" "$((PASS+FAIL))"
else
  printf "\033[1;31m✗ %s passed / %s FAILED.\033[0m\n" "$PASS" "$FAIL"
fi
rm -rf "$H" 2>/dev/null || true
[ "$FAIL" -eq 0 ]
