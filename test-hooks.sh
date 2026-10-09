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

echo ""
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32m✅ All hooks healthy: %s/%s passed.\033[0m\n" "$PASS" "$((PASS+FAIL))"
else
  printf "\033[1;31m✗ %s passed / %s FAILED.\033[0m\n" "$PASS" "$FAIL"
fi
rm -rf "$H" 2>/dev/null || true
[ "$FAIL" -eq 0 ]
