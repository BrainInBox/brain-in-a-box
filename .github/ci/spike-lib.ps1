# Helpers for the gbrain-on-Windows spike (CI only). Dot-source it: . .github/ci/spike-lib.ps1
$ErrorActionPreference = 'Stop'
$Results = Join-Path $env:RUNNER_TEMP 'spike-results.jsonl'

function Note([string]$Line) {
    Write-Host $Line
    if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $Line -Encoding utf8 }
}

# A check the verdict rests on: recorded, and summed up by the last step of the job.
function Check([string]$Name, [bool]$Pass, [string]$Detail = '') {
    $mark = if ($Pass) { 'PASS' } else { 'FAIL' }
    Note "- **$mark** $Name $(if ($Detail) { "- $Detail" })"
    (@{ name = $Name; pass = $Pass; detail = $Detail } | ConvertTo-Json -Compress) | Add-Content -Path $Results -Encoding utf8
}

# Runs a native command with stdin closed (like a scheduled task), a hard timeout,
# and its wall time. A read that prints and then never exits shows up as TimedOut.
function Invoke-Timed {
    param([string]$Label, [string]$Exe, [string[]]$ArgList = @(), [int]$TimeoutSec = 300, [string]$WorkDir = $PWD.Path)
    $psi = [Diagnostics.ProcessStartInfo]::new($Exe)
    foreach ($a in $ArgList) { $psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = [Diagnostics.Process]::Start($psi)
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $timedOut = -not $p.WaitForExit($TimeoutSec * 1000)
    if ($timedOut) { $p.Kill($true); $p.WaitForExit() }
    $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $r = [pscustomobject]@{ Label = $Label; ExitCode = $(if ($timedOut) { $null } else { $p.ExitCode }); Seconds = $secs; TimedOut = $timedOut; Out = $out.Result; Err = $err.Result }
    Write-Host "::group::$Label  (exit=$($r.ExitCode) $($secs)s timedOut=$timedOut)"
    Write-Host "--- stdout ---"; Write-Host $r.Out
    Write-Host "--- stderr ---"; Write-Host $r.Err
    Write-Host "::endgroup::"
    Note "- ``$Label``: exit $($r.ExitCode), $secs s$(if ($timedOut) { ', TIMED OUT (killed)' })"
    $r
}

function Gbrain-Exe {
    $c = Get-Command gbrain -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    throw 'gbrain not on PATH'
}

function New-TestVault([string]$Dir) {
    New-Item -ItemType Directory -Force "$Dir\people", "$Dir\projects", "$Dir\journal" | Out-Null
    $utf8 = New-Object Text.UTF8Encoding $false
    [IO.File]::WriteAllText("$Dir\people\alice.md", "---`ntitle: Alice Martin`ntype: person`n---`n# Alice Martin`n`nAlice is a gardener who grows tomatoes and basil in her greenhouse every summer.`n", $utf8)
    [IO.File]::WriteAllText("$Dir\projects\rocket.md", "---`ntitle: Rocket launch`ntype: project`n---`n# Rocket launch`n`nThe rocket engine test is scheduled for March; the propulsion team checks the fuel valves.`n", $utf8)
    [IO.File]::WriteAllText("$Dir\notes.md", "# Weekly notes`n`nPaid the electricity invoice and renewed the car insurance.`n", $utf8)
    [IO.File]::WriteAllText("$Dir\journal\r$([char]0xE9)union-$([char]0xE9)t$([char]0xE9).md", "# R$([char]0xE9)union d'$([char]0xE9)t$([char]0xE9)`n`nOn a parl$([char]0xE9) du budget de la cantine et des f$([char]0xEA)tes de fin d'ann$([char]0xE9)e.`n", $utf8)
}

# The fake Voyage (.github/ci/fake_voyage.py) on 127.0.0.1:8899, started once and reused.
function Start-FakeVoyage {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', 8899); $c.Close(); return } catch {}
    $log = Join-Path $env:RUNNER_TEMP 'fake-voyage.log'
    Start-Process python -ArgumentList @('-I', (Join-Path $PWD '.github\ci\fake_voyage.py'), '8899') -RedirectStandardOutput $log -RedirectStandardError "$log.err" | Out-Null
    Start-Sleep -Seconds 2
}

# Points GBRAIN_HOME at $HomeDir and writes the config install.sh writes (key in config.json,
# never in argv), with Voyage redirected to the fake.
function Use-FakeVoyageHome([string]$HomeDir) {
    $env:GBRAIN_HOME = $HomeDir
    $gd = Join-Path $HomeDir '.gbrain'
    New-Item -ItemType Directory -Force $gd | Out-Null
    $cfg = @{ voyage_api_key = 'ci-fake-key-not-a-secret'; provider_base_urls = @{ voyage = 'http://127.0.0.1:8899/v1' } } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $gd 'config.json'), $cfg, (New-Object Text.UTF8Encoding $false))
}
