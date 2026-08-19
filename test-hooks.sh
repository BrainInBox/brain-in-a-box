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
echo '{"prompt":"no, actually do it the other way"}' | HOME="$H" python3 "$HB/correction-detector.py" 2>/dev/null | grep -q "CORRECTION DETECTED" && ok "fires on a real correction" || no "did not fire"
out=$(echo '{"prompt":"build me a dashboard"}' | HOME="$H" python3 "$HB/correction-detector.py" 2>/dev/null); [ -z "$out" ] && ok "stays silent on a neutral prompt" || no "fired on a neutral prompt"

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

echo "════ 5) daily-reflection (claude stubbed) ════"
cat > "$H/.local/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo called > "$HOME/.claude/logs/claude-stub-called.txt"
printf '%s' "$*" > "$HOME/.claude/logs/claude-stub-prompt.txt"
exit 0
STUB
chmod +x "$H/.local/bin/claude"
rm -f "$TMPDIR"/brain-daily-reflection-$DAY-*.lock 2>/dev/null   # clear debounce lock from prior runs
HOME="$H" python3 "$HB/daily-reflection.py" 2>/dev/null
[ -f "$H/.claude/logs/claude-stub-called.txt" ] && ok "cron flow ran (read logs → built prompt → invoked claude)" || no "cron flow did not run"
grep -q "journal summary" "$H/.claude/logs/claude-stub-prompt.txt" 2>/dev/null && ok "prompt is correct" || no "prompt wrong"
rm -f "$H/.claude/logs/sessions-$DAY.jsonl"
HOME="$H" python3 "$HB/daily-reflection.py" 2>/dev/null && ok "exits gracefully when there's nothing to do" || no "crashed when nothing to do"

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
cat > "$H/.bun/bin/bun" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
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

echo ""
if [ "$FAIL" -eq 0 ]; then
  printf "\033[1;32m✅ All hooks healthy: %s/%s passed.\033[0m\n" "$PASS" "$((PASS+FAIL))"
else
  printf "\033[1;31m✗ %s passed / %s FAILED.\033[0m\n" "$PASS" "$FAIL"
fi
rm -rf "$H" 2>/dev/null || true
[ "$FAIL" -eq 0 ]
