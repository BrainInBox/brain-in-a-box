# Spike, part 2: a vault of a realistic size, the nightly cycle as the macOS nightly runs it
# (sync --no-embed, embed --stale --max-usd), then what happens when two gbrain run at once
# and when one is killed mid-write (PC put to sleep, window closed).
. "$PSScriptRoot\spike-lib.ps1"
$ErrorActionPreference = 'Continue'  # git and taskkill write to stderr; the checks carry the verdict
$g = Gbrain-Exe
Start-FakeVoyage
Use-FakeVoyageHome "$env:RUNNER_TEMP\scale"
$vault = "$env:RUNNER_TEMP\scale\Brain"
$N = 1000

$rng = [Random]::new(42)
$words = 'meeting budget garden family travel invoice project client recipe doctor school music book idea plan health car house friend sport weather market email phone code design report contract holiday birthday insurance tax'.Split(' ')
New-Item -ItemType Directory -Force "$vault\notes" | Out-Null
$utf8 = New-Object Text.UTF8Encoding $false
for ($i = 0; $i -lt $N; $i++) {
    $body = (1..120 | ForEach-Object { $words[$rng.Next($words.Count)] }) -join ' '
    [IO.File]::WriteAllText("$vault\notes\note-$i.md", "# Note $i`n`n$body`n", $utf8)
}
[IO.File]::WriteAllText("$vault\notes\lighthouse.md", "# The lighthouse`n`nThe lighthouse keeper logs every storm and repairs the lamp lens before winter.`n", $utf8)
git -C $vault init -q
git -C $vault add -A
git -C $vault -c user.email=brain@local -c user.name=brain commit -q -m init

Note "### $N pages: nightly cycle"
$r = Invoke-Timed 'init voyage:voyage-4 (scale)' $g @('init', '--pglite', '--embedding-model', 'voyage:voyage-4', '--embedding-dimensions', '1024', '--search-mode', 'balanced', '--json') 600
$r = Invoke-Timed "sync --repo --no-pull --no-embed ($N pages)" $g @('sync', '--repo', $vault, '--no-pull', '--no-embed') 1200
Check "sync of $N pages exits 0" ($r.ExitCode -eq 0) "$($r.Seconds)s"
$r = Invoke-Timed "embed --stale --max-usd 0.10 ($N pages)" $g @('embed', '--stale', '--max-usd', '0.10') 1200
Check "embed of $N pages exits 0" ($r.ExitCode -eq 0) "$($r.Seconds)s"
foreach ($q in 1..3) {
    $r = Invoke-Timed "query #$q 'lighthouse keeper storm'" $g @('query', 'lighthouse keeper storm') 120
}
$first = ($r.Out -split "`n" | Select-String '^\[' | Select-Object -First 1)
Check "query over $N pages ranks the needle first" ("$first" -match 'lighthouse') "$first ($($r.Seconds)s)"

# Incremental: 20 edited notes, committed, then the nightly pair again.
for ($i = 0; $i -lt 20; $i++) { Add-Content "$vault\notes\note-$i.md" "`nedited lighthouse supplement $i" }
git -C $vault -c user.email=brain@local -c user.name=brain commit -q -am edit
$r = Invoke-Timed 'incremental sync (20 edits)' $g @('sync', '--repo', $vault, '--no-pull', '--no-embed') 600
Check 'incremental sync exits 0' ($r.ExitCode -eq 0) "$($r.Seconds)s"
$r = Invoke-Timed 'incremental embed --stale' $g @('embed', '--stale', '--max-usd', '0.10') 600
Check 'incremental embed exits 0' ($r.ExitCode -eq 0) (($r.Out + $r.Err) -split "`n" | Select-String 'Embedded' | Select-Object -First 1)
$r = Invoke-Timed 'dream (part of the macOS nightly; no LLM key)' $g @('dream') 600
Note "dream: exit $($r.ExitCode) (informational, needs an LLM key for most phases)"

# Two at once: a query while a write holds the brain.
Note "### concurrency and crash"
function Touch-All([string]$tag) {
    Get-ChildItem "$vault\notes" -Filter 'note-*.md' | ForEach-Object { Add-Content $_.FullName "`n$tag" }
    git -C $vault -c user.email=brain@local -c user.name=brain commit -q -am $tag
}
Touch-All 'pass-two'
$wlog = "$env:RUNNER_TEMP\write.log"
$w = Start-Process $g -ArgumentList @('sync', '--repo', "`"$vault`"", '--no-pull', '--no-embed') -RedirectStandardOutput $wlog -RedirectStandardError "$wlog.err" -PassThru
$null = $w.Handle  # without it, ExitCode reads empty once the process is gone
Start-Sleep -Milliseconds 1500
$writing = -not $w.HasExited
$r = Invoke-Timed 'query while a sync writes' $g @('query', 'lighthouse keeper storm') 180
Note "write still running when the query started: $writing"
Check 'a query during a write answers or waits, no crash' (($r.ExitCode -eq 0) -or (-not $writing)) "exit=$($r.ExitCode) $($r.Seconds)s; stderr: $((($r.Err -split "`n") | Select-String 'lock|busy|held' | Select-Object -First 2) -join ' / ')"
$w.WaitForExit(600000) | Out-Null
Check 'the concurrent sync still completes' ($w.ExitCode -eq 0) "exit=$($w.ExitCode)"
Get-Content "$wlog.err" -ErrorAction SilentlyContinue | Select-Object -Last 5 | ForEach-Object { Write-Host "sync stderr: $_" }

# Killed mid-write: taskkill /F /T is what a closed window or a power cut leaves behind.
Touch-All 'pass-three'
$k = Start-Process $g -ArgumentList @('sync', '--repo', "`"$vault`"", '--no-pull', '--no-embed') -PassThru
$null = $k.Handle
Start-Sleep -Milliseconds 1500
$killedMidWrite = -not $k.HasExited
taskkill /F /T /PID $k.Id 2>&1 | Write-Host
Start-Sleep -Seconds 1
Note "sync killed mid-write: $killedMidWrite"
Get-ChildItem "$env:RUNNER_TEMP\scale\.gbrain" -Force | Format-Table Name, Length, LastWriteTime
$r = Invoke-Timed 'query right after the kill' $g @('query', 'lighthouse keeper storm') 180
Check 'query works after a writer was killed' ($r.ExitCode -eq 0 -and $r.Out -match 'lighthouse') "exit=$($r.ExitCode) $($r.Seconds)s; $((($r.Err -split "`n") | Select-String 'lock|stale|corrupt|WAL|repair' | Select-Object -First 3) -join ' / ')"
$r = Invoke-Timed 'sync again after the kill' $g @('sync', '--repo', $vault, '--no-pull', '--no-embed') 600
Check 'sync recovers after a writer was killed' ($r.ExitCode -eq 0) "exit=$($r.ExitCode) $($r.Seconds)s"
$r = Invoke-Timed 'doctor --json after the kill' $g @('doctor', '--json') 300
try {
    $d = $r.Out | ConvertFrom-Json
    foreach ($n in 'connection', 'pgvector', 'embeddings', 'pglite_health', 'lock') {
        $c = $d.checks | Where-Object name -like "*$n*"
        $c | ForEach-Object { Write-Host "doctor $($_.name): $($_.status) - $($_.message)" }
    }
    $cn = $d.checks | Where-Object name -eq 'connection'
    Check 'doctor: brain healthy after the kill' ($cn.status -eq 'ok') "$($cn.status): $($cn.message)"
} catch { Check 'doctor --json parses after the kill' $false "$_" }
Get-ChildItem "$env:RUNNER_TEMP\scale\.gbrain\brain.pglite" -Recurse -File | Measure-Object Length -Sum | ForEach-Object { Note "brain.pglite on disk: $($_.Count) files, $([math]::Round($_.Sum/1MB)) MB" }
