# Claude alone, install.ps1 says and does what master's does. Both re-run over the install in place,
# master's first, then this one's: on screen the same lines, in install.log the same commands, on disk
# the same files, but for the kit's copies of the sources this change touches (each run puts its own
# version back, backs the other up and says so, with the same names both ways) and their hashes in
# installed.json. This one's run leaves everything as it found it. The user's own Codex (~\.codex, made
# before the first install) is untouched, and nothing goes into ~\.agents. What the change adds (a source
# or a skill master has not) is the one more thing on screen: the lines that name it, each one there;
# its files, left by master's run, stay as this one put them. A skill master has too and this change
# edits: its files differ, and the skills' manifest.
# Used by test.yml (job windows), without agents.json and with Claude chosen, from the repository's root:
#   & .github/ci/same-as-master.ps1
$ErrorActionPreference = "Stop"
$H = $env:USERPROFILE
$Mine = (Resolve-Path .).Path
$T = Join-Path $env:RUNNER_TEMP "vs-master"
if (-not (Test-Path -LiteralPath "$T\master\install.ps1")) {
    New-Item -ItemType Directory $T -Force | Out-Null
    git fetch -q --depth 1 origin master
    if ($LASTEXITCODE) { throw "git fetch of master failed" }
    git archive --format=zip -o "$T\master.zip" FETCH_HEAD
    if ($LASTEXITCODE) { throw "git archive of master failed" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory("$T\master.zip", "$T\master")
}
$Log = "$H\.brain-in-a-box\install.log"
# What the kit installs, apart from gbrain's own state and the search index, which every run rebuilds.
function Snap {
    $m = [ordered]@{}
    foreach ($r in "$H\.claude", "$H\.brain-in-a-box", "$H\.local\bin", "$H\.codex", "$H\.agents", "$H\Documents\Brain") {
        if (-not (Test-Path -LiteralPath $r)) { continue }
        Get-ChildItem -LiteralPath $r -Recurse -File -Force | Where-Object {
            $_.FullName -notlike "$H\.brain-in-a-box\backups\*" -and $_.FullName -ne $Log -and $_.FullName -notlike "$H\Documents\Brain\.git\*"
        } | Sort-Object FullName | ForEach-Object { $m[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName).Hash }
    }
    foreach ($f in Get-ChildItem -LiteralPath "$H\.brain-search" -File -Filter *.py) { $m[$f.FullName] = (Get-FileHash -LiteralPath $f.FullName).Hash }
    foreach ($t in Get-ScheduledTask -TaskName BrainNightly, BrainReflection -ErrorAction SilentlyContinue) {
        $m["task $($t.TaskName)"] = "$($t.Actions.Execute) $($t.Actions.Arguments)"
    }
    return $m
}
function Run([string]$Repo) {
    $from = (Get-Item -LiteralPath $Log).Length
    $out = (powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$Repo\install.ps1" 2>&1 | Out-String)
    if ($LASTEXITCODE) { Write-Host $out; throw "install.ps1 of $Repo failed ($LASTEXITCODE)" }
    $all = [IO.File]::ReadAllBytes($Log)
    # Not $log: PowerShell's names ignore case, and Snap, called from here, would read it as $Log.
    $added = [Text.Encoding]::UTF8.GetString($all, [int]$from, $all.Length - [int]$from)
    $norm = { param($s) ($s.Replace($Repo, "<kit>") -replace '\d{4}-\d\d-\d\d \d\d:\d\d:\d\d', '<date>') -replace 'backups\\\d{8}-\d{6}', 'backups\<date>' }
    return @{ Out = (& $norm $out); Calls = @((& $norm $added) -split "`r?`n" | Where-Object { $_ -like '> *' }) -join "`n"; Snap = (Snap) }
}
function Differ($a, $b) { return @(@($a.Keys) + @($b.Keys) | Sort-Object -Unique | Where-Object { $a[$_] -ne $b[$_] }) }

$backups = @(Get-ChildItem -LiteralPath "$H\.brain-in-a-box\backups" -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
$before = Snap
$m = Run "$T\master"
$b = Run $Mine
# The sources this change touches, among those install.ps1 copies next to the hooks, and into ~\.brain-search:
# those master has too and this change alters, and those it adds.
$touched = @(); $added = @()
foreach ($f in @(Get-ChildItem "$Mine\engine\hooks" -Filter *.py -File) + @(Get-Item "$Mine\engine\nightly\windows-nightly.py") +
               @(Get-ChildItem "$Mine\engine\search" -Filter *.py -File)) {
    $theirs = Join-Path "$T\master" ($f.FullName.Substring($Mine.Length + 1))
    $copy = if ($f.Directory.Name -eq "search") { "$H\.brain-search\$($f.Name)" } else { "$H\.claude\hooks\brain\$($f.Name)" }
    if (-not (Test-Path -LiteralPath $theirs)) { $added += $copy }
    elseif ((Get-FileHash $theirs).Hash -ne (Get-FileHash $f.FullName).Hash) { $touched += $copy }
}
$newSkills = @(Get-ChildItem "$Mine\skills" -Directory | Where-Object { -not (Test-Path -LiteralPath "$T\master\skills\$($_.Name)") } | ForEach-Object { $_.Name })
$editedSkills = @(foreach ($f in Get-ChildItem "$Mine\skills" -Recurse -File) {
    $theirs = Join-Path "$T\master" ($f.FullName.Substring($Mine.Length + 1))
    if ((Test-Path -LiteralPath $theirs) -and (Get-FileHash $theirs).Hash -ne (Get-FileHash $f.FullName).Hash) {
        "$H\.claude\skills\" + $f.FullName.Substring("$Mine\skills\".Length)
    }
})
Write-Host "the kit's copies this change touches: $($touched -join ', '); adds: $(@($added) + @($newSkills) -join ', ')"
# The lines that name what it adds: the launcher of a source it adds ("[OK] <name> installe (..."), and
# install_skills.py's line for a skill it adds: each one must be there.
$skillLines = @(foreach ($n in $newSkills) { '^\s*(installed|up to date) +' + [regex]::Escape($n) + '\s*$' })
$addedLines = @(foreach ($n in @($added | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_) } | Where-Object { -not $_.StartsWith("_") })) {
    '^\s*\[OK\] ' + [regex]::Escape($n) + ' installe \('
}) + $skillLines
$lines = { param($s) @($s -split "`r?`n") }
$bLines = & $lines $b.Out
foreach ($p in $addedLines) {
    if (-not @($bLines | Where-Object { $_ -cmatch $p }).Count) { throw "Claude alone: install.ps1 does not say what this change adds ($p)" }
}
$bKept = @($bLines | Where-Object { $l = $_; -not @($addedLines | Where-Object { $l -cmatch $_ }).Count })
if (((& $lines $m.Out) -join "`n") -cne ($bKept -join "`n")) {
    Compare-Object (& $lines $m.Out) $bKept | Format-Table -AutoSize -Wrap | Out-String -Width 400 | Write-Host
    throw "Claude alone: install.ps1 does not say what master's says (<= master, => this change)"
}
if ($m.Calls -cne $b.Calls) {
    Compare-Object ($m.Calls -split "`n") ($b.Calls -split "`n") | Format-Table -AutoSize -Wrap | Out-String -Width 400 | Write-Host
    throw "Claude alone: install.ps1 does not run what master's runs (install.log)"
}
$again = Differ $before $b.Snap
if ($again.Count) { throw "Claude alone: this change's run did not leave the install as it found it: $($again -join ', ')" }
$apart = Differ $m.Snap $b.Snap
$want = @($touched | Where-Object { Test-Path -LiteralPath $_ }) + @(if ($touched.Count) { "$H\.brain-in-a-box\installed.json" }) +
        $editedSkills + @(if ($editedSkills.Count) { "$H\.brain-in-a-box\skills.json" })
# The reflection's task starts pythonw.exe (no black window at 12:00 and 23:00), master's python.exe: the one
# difference of its own a change may make, said here, until master starts it so too.
if ((Get-Content -Raw "$T\master\install.ps1").Contains('Register-BrainTask "BrainReflection" $Py ') -and $m.Snap["task BrainReflection"] -ne $b.Snap["task BrainReflection"]) {
    if ($b.Snap["task BrainReflection"] -ne ($m.Snap["task BrainReflection"] -replace '\\python\.exe ', '\pythonw.exe ')) {
        throw "Claude alone: BrainReflection starts $($b.Snap['task BrainReflection']), master's $($m.Snap['task BrainReflection'])"
    }
    $want += "task BrainReflection"
}
if ((($apart | Sort-Object) -join "`n") -cne (($want | Sort-Object) -join "`n")) {
    throw "Claude alone: master's files and this change's differ in $($apart -join ', '), not just in $($want -join ', ')"
}
# What it adds, copied by the install before these two runs: there, and master's run left it as it was.
foreach ($a in @($added) + @($newSkills | ForEach-Object { "$H\.claude\skills\$_\SKILL.md" })) {
    if (-not $b.Snap.Contains($a) -or $m.Snap[$a] -cne $b.Snap[$a]) { throw "Claude alone: $a is not in place, or master's run changed it" }
}
Write-Host "Claude alone: the same lines on screen but the $($addedLines.Count) naming what it adds, the same $(@($b.Calls -split "`n").Count) commands in install.log, the same $($b.Snap.Count) files but the $($touched.Count) touched"
$now = @(Get-ChildItem "$H\.codex" -Recurse -File -Force | ForEach-Object { "$($_.FullName) $((Get-FileHash $_.FullName).Hash)" } | Sort-Object)
if (($now -join "`n") -cne ((Get-Content "$env:RUNNER_TEMP\user-codex.txt") -join "`n")) { throw "Claude alone wrote into the user's own ~\.codex" }
if (Test-Path -LiteralPath "$H\.agents") { throw "Claude alone wrote ~\.agents" }
# These two runs' backups go: later steps count the ones they expect.
Get-ChildItem -LiteralPath "$H\.brain-in-a-box\backups" -Directory -ErrorAction SilentlyContinue | Where-Object { $backups -notcontains $_.FullName } |
    Remove-Item -Recurse -Force
