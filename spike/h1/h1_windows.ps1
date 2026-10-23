# Preuve H1 sous Windows (jetable) : Hermes installé comme Bibo le ferait, à côté d'un Hermes
# « de la personne » (sentinelles), mesuré, piloté par sa TUI gateway, purgé, désinstallé.
param([switch]$Mutations)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Here = $PSScriptRoot
$Sha = $env:H1_HERMES_COMMIT
$Expected = $env:H1_INSTALL_PS1_SHA256
$Out = Join-Path $env:RUNNER_TEMP "h1out"
New-Item -ItemType Directory -Force $Out | Out-Null
$Root = Join-Path $env:LOCALAPPDATA "Bibo\hermes"
$UserHome = Join-Path $env:LOCALAPPDATA "hermes"
$AppDataHermes = Join-Path $env:APPDATA "Hermes"
$Xdg = @((Join-Path $HOME ".cache\hermes"), (Join-Path $HOME ".local\share\hermes"))
$R = [ordered]@{ os = (Get-CimInstance Win32_OperatingSystem).Caption; build = [Environment]::OSVersion.Version.ToString(); commit = $Sha; root = $Root }

function Save { $R | ConvertTo-Json -Depth 14 | Set-Content (Join-Path $Out "windows.json") -Encoding UTF8 }
function Py([string[]]$A) {
    $o = & python @A
    if ($LASTEXITCODE) { return @{ error = "python $($A[0]) $($A[1]) exit $LASTEXITCODE" } }
    return (($o -join "`n") | ConvertFrom-Json)
}
function Now { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 }
function EnvKey { [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true) }
function RawUserPath { $k = EnvKey; try { $k.GetValue("Path", "", "DoNotExpandEnvironmentNames") } finally { $k.Close() } }
function UserPathKind { $k = EnvKey; try { if ($k.GetValue("Path") -ne $null) { "$($k.GetValueKind('Path'))" } else { "absent" } } finally { $k.Close() } }
function EnvSnapshot {
    $k = EnvKey; $h = [ordered]@{}
    try { foreach ($n in $k.GetValueNames()) { $h[$n] = "$($k.GetValueKind($n)):" + $k.GetValue($n, "", "DoNotExpandEnvironmentNames") } } finally { $k.Close() }
    return $h
}
function DiffMap($a, $b) {
    $d = [ordered]@{}
    foreach ($n in @($a.Keys) + @($b.Keys) | Sort-Object -Unique) {
        $x = $a[$n]; $y = $b[$n]
        if ("$x" -ne "$y") { $d[$n] = [ordered]@{ before = $x; after = $y } }
    }
    return $d
}
function Resolve-Hermes {
    $all = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::ExpandEnvironmentVariables((RawUserPath))
    foreach ($e in ($all -split ";")) {
        if (-not $e) { continue }
        foreach ($n in "hermes.exe", "hermes.cmd", "hermes.bat") {
            $p = Join-Path $e $n
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    return $null
}
function Sentinels {
    $files = Py @("$Here\h1_disk.py", "hashes", $UserHome, $AppDataHermes, $Xdg[0], $Xdg[1])
    $h = [ordered]@{}
    foreach ($p in $files.PSObject.Properties) { $h[$p.Name] = $p.Value }
    $h["HKCU:HERMES_HOME"] = [Environment]::GetEnvironmentVariable("HERMES_HOME", "User")
    $h["HKCU:HERMES_GIT_BASH_PATH"] = [Environment]::GetEnvironmentVariable("HERMES_GIT_BASH_PATH", "User")
    $h["HKCU:Path has %LOCALAPPDATA%\hermes\bin"] = ((RawUserPath) -split ";") -contains "%LOCALAPPDATA%\hermes\bin"
    $h["HKCU:Path kind"] = UserPathKind
    $h["task Hermes_Gateway"] = [bool](Get-ScheduledTask -TaskName "Hermes_Gateway" -ErrorAction SilentlyContinue)
    return $h
}
function DefenderState {
    try {
        $s = Get-MpComputerStatus; $p = Get-MpPreference
        return [ordered]@{ AMServiceEnabled = $s.AMServiceEnabled; AntivirusEnabled = $s.AntivirusEnabled
            RealTimeProtectionEnabled = $s.RealTimeProtectionEnabled; BehaviorMonitorEnabled = $s.BehaviorMonitorEnabled
            IoavProtectionEnabled = $s.IoavProtectionEnabled; IsTamperProtected = $s.IsTamperProtected
            AMRunningMode = $s.AMRunningMode; SignatureVersion = $s.AntivirusSignatureVersion; SignatureAge = $s.AntivirusSignatureAge
            ExclusionPath = @($p.ExclusionPath); ExclusionProcess = @($p.ExclusionProcess)
            DisableRealtimeMonitoring = $p.DisableRealtimeMonitoring; MAPSReporting = $p.MAPSReporting }
    } catch { return @{ error = "$_" } }
}
function Eicar([string]$label) {
    # Témoin : un fichier EICAR, assemblé ici pour que le script lui-même ne soit pas un échantillon.
    $parts = @('X5O!P%@AP[4\PZX54(P^)7CC)7}$', 'EICAR-STANDARD-ANTIVIRUS', '-TEST-FILE!$H+H*')
    $f = Join-Path $env:RUNNER_TEMP "h1-eicar-$label.com"
    try { [IO.File]::WriteAllText($f, ($parts -join "")) } catch { return "write blocked: $_" }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path $f)) { return "quarantined" }
        try { $null = [IO.File]::ReadAllBytes($f) } catch { return "access denied (blocked)" }
        Start-Sleep -Milliseconds 500
    }
    Remove-Item $f -Force -ErrorAction SilentlyContinue
    return "NOT detected (realtime off)"
}

# ---------------------------------------------------------------- sentinelles : la personne a déjà Hermes
New-Item -ItemType Directory -Force (Join-Path $UserHome "bin"), $AppDataHermes, $Xdg[0], $Xdg[1] | Out-Null
"# config Hermes de la personne (sentinelle)" | Set-Content (Join-Path $UserHome "config.yaml")
"sentinelle" | Set-Content (Join-Path $UserHome "sentinel.txt")
"@echo hermes de la personne" | Set-Content (Join-Path $UserHome "bin\hermes.cmd")
"sentinelle" | Set-Content (Join-Path $AppDataHermes "sentinel.txt")
foreach ($d in $Xdg) { "sentinelle" | Set-Content (Join-Path $d "sentinel.txt") }
$k = EnvKey
$k.SetValue("Path", "%LOCALAPPDATA%\hermes\bin;" + (RawUserPath), [Microsoft.Win32.RegistryValueKind]::ExpandString)
$k.SetValue("HERMES_HOME", $UserHome, [Microsoft.Win32.RegistryValueKind]::String)
$k.SetValue("HERMES_GIT_BASH_PATH", "C:\fake\bash.exe", [Microsoft.Win32.RegistryValueKind]::String)
$k.Close()
Register-ScheduledTask -TaskName "Hermes_Gateway" -Action (New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c exit 0") -Force | Out-Null
$env:HERMES_HOME = $UserHome             # l'environnement hérité de la personne
$env:H1_USER_HERMES_HOME = $UserHome
$S0 = Sentinels; $Env0 = EnvSnapshot
$Tasks0 = @(Get-ScheduledTask | ForEach-Object { $_.TaskPath + $_.TaskName }); $Svc0 = @(Get-Service | ForEach-Object Name)
$R.user_path_before = @((RawUserPath) -split ";"); $R.user_path_kind_before = UserPathKind
$R.hermes_resolves_before = Resolve-Hermes

# ---------------------------------------------------------------- Defender armé, avec témoin
$R.defender_initial = DefenderState
$R.eicar_initial = Eicar "initial"
try {
    $p = Get-MpPreference
    if ($p.ExclusionPath) { Remove-MpPreference -ExclusionPath $p.ExclusionPath -ErrorAction Stop }
    if ($p.ExclusionProcess) { Remove-MpPreference -ExclusionProcess $p.ExclusionProcess -ErrorAction Stop }
    Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false -DisableIOAVProtection $false -SubmitSamplesConsent NeverSend -ErrorAction Stop
} catch { $R.defender_enable_error = "$_" }
$j = Start-Job { Update-MpSignature -ErrorAction Stop }
if (Wait-Job $j -Timeout 360) { try { Receive-Job $j -ErrorAction Stop | Out-Null; $R.defender_update = "ok" } catch { $R.defender_update = "$_" } } else { Stop-Job $j; $R.defender_update = "timeout" }
$R.defender_armed = DefenderState
$R.eicar_armed = Eicar "armed"
$tDef = Get-Date
Save

# ---------------------------------------------------------------- installation épinglée, silencieuse
$inst = Join-Path $Out "install.ps1"
Invoke-WebRequest "https://raw.githubusercontent.com/NousResearch/hermes-agent/$Sha/scripts/install.ps1" -OutFile $inst -UseBasicParsing
$got = (Get-FileHash $inst -Algorithm SHA256).Hash.ToLower()
$R.install_ps1_sha256 = @{ expected = $Expected; got = $got; ok = ($got -eq $Expected) }
if ($got -ne $Expected) { Save; throw "install.ps1 SHA-256 mismatch" }
$free0 = (Get-PSDrive C).Free
$t0 = Now
$sw = [Diagnostics.Stopwatch]::StartNew()
$instArgs = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $inst, "-Commit", $Sha,
          "-NonInteractive", "-SkipBrowser", "-SkipComputerUse", "-HermesHome", $Root)
$proc = Start-Process powershell.exe -ArgumentList $instArgs -WindowStyle Hidden -Wait -PassThru `
    -RedirectStandardOutput (Join-Path $Out "install.out.log") -RedirectStandardError (Join-Path $Out "install.err.log")
$sw.Stop()
$R.install = [ordered]@{ seconds = [math]::Round($sw.Elapsed.TotalSeconds); exit = $proc.ExitCode
    c_drive_used_mb = [math]::Round(($free0 - (Get-PSDrive C).Free) / 1MB)
    out_tail = @(Get-Content (Join-Path $Out "install.out.log") -Tail 30); err_tail = @(Get-Content (Join-Path $Out "install.err.log") -Tail 15) }
Save
if ($proc.ExitCode -ne 0) { throw "install failed ($($proc.ExitCode))" }

$R.size_root = Py @("$Here\h1_disk.py", "size", $Root)
$R.written_outside_root = Py @("$Here\h1_disk.py", "newfiles", "$t0", "--root", $HOME, "--root", $env:ProgramData,
    "--exclude", $Root, "--exclude", $Out)
$S1 = Sentinels; $Env1 = EnvSnapshot
$R.install_user_env_changes = DiffMap $Env0 $Env1
$R.install_sentinel_changes = DiffMap $S0 $S1
$R.install_new_tasks = @(Get-ScheduledTask | ForEach-Object { $_.TaskPath + $_.TaskName } | Where-Object { $Tasks0 -notcontains $_ })
$R.install_new_services = @(Get-Service | ForEach-Object Name | Where-Object { $Svc0 -notcontains $_ })
$R.user_path_after_install = @((RawUserPath) -split ";"); $R.user_path_kind_after_install = UserPathKind
$R.path_head_is_bibo_bin = (((RawUserPath) -split ";")[0].TrimEnd("\") -eq (Join-Path $Root "bin"))
$R.hermes_resolves_after_install = Resolve-Hermes

# Defender : détections pendant l'installation, puis un scan du dossier.
$R.defender_detections_install = @(Get-MpThreatDetection -ErrorAction SilentlyContinue | Where-Object { $_.InitialDetectionTime -ge $tDef } |
    ForEach-Object { [ordered]@{ resources = @($_.Resources); action = $_.ActionSuccess; threat = $_.ThreatID } })
$uvs = @(Get-ChildItem -Path $Root -Filter uv.exe -Recurse -ErrorAction SilentlyContinue | ForEach-Object FullName)
$R.uv_exe_present = $uvs
$sw = [Diagnostics.Stopwatch]::StartNew()
$j = Start-Job -ArgumentList $Root { param($r) Start-MpScan -ScanType CustomScan -ScanPath $r -ErrorAction Stop }
if (Wait-Job $j -Timeout 1500) { try { Receive-Job $j -ErrorAction Stop | Out-Null; $scan = "done" } catch { $scan = "$_" } } else { Stop-Job $j; $scan = "timeout" }
$R.defender_scan = [ordered]@{ result = $scan; seconds = [math]::Round($sw.Elapsed.TotalSeconds)
    detections = @(Get-MpThreatDetection -ErrorAction SilentlyContinue | Where-Object { $_.InitialDetectionTime -ge $tDef } |
        ForEach-Object { [ordered]@{ resources = @($_.Resources); threat = $_.ThreatID } }) }
$R.defender_end = DefenderState
$R.eicar_end = Eicar "end"
Save

# ---------------------------------------------------------------- la parade de Bibo : PATH rendu, appel par chemin complet
$launcher = @((Join-Path $Root "hermes-agent\.hermes\bin\hermes.exe"), (Join-Path $Root "bin\hermes.exe"), (Join-Path $Root "bin\hermes.cmd")) |
    Where-Object { Test-Path $_ } | Select-Object -First 1
$R.launcher = $launcher
$bin = (Join-Path $Root "bin")
$k = EnvKey
$kind = $k.GetValueKind("Path")
$kept = @(($k.GetValue("Path", "", "DoNotExpandEnvironmentNames") -split ";") | Where-Object { $_ -and ($_.TrimEnd("\") -ne $bin) })
$k.SetValue("Path", ($kept -join ";"), $kind)
$k.Close()
$R.user_path_after_fix = @((RawUserPath) -split ";"); $R.user_path_kind_after_fix = UserPathKind
$R.hermes_resolves_after_fix = Resolve-Hermes
$env:HERMES_HOME = $Root
$v = & $launcher --version 2>&1 | Out-String
$R.full_path_call = @{ exit = $LASTEXITCODE; first_line = ($v -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1) }
$R.path_readded_after_version = (((RawUserPath) -split ";") | Where-Object { $_.TrimEnd("\") -eq $bin }).Count -gt 0
$env:HERMES_HOME = $UserHome
Save

# ---------------------------------------------------------------- la gateway, contre le faux fournisseur
& python "$Here\h1_probe.py" --root $Root --out $Out --scan-home *> (Join-Path $Out "probe-main.log")
$R.probe_main_exit = $LASTEXITCODE
$rep = Join-Path $Out "report-main.json"
$R.probe_main = if (Test-Path $rep) { Get-Content $rep -Raw | ConvertFrom-Json } else { @{ crash = @(Get-Content (Join-Path $Out "probe-main.log") -Tail 40) } }
$R.path_readded_by_hermes = (((RawUserPath) -split ";") | Where-Object { $_.TrimEnd("\") -eq $bin }).Count -gt 0
Save

if ($Mutations) {
    $R.mutations = [ordered]@{}
    foreach ($m in "tools_default", "env_toolsets", "approvals_off", "approvals_smart", "yolo_env", "no_caps",
                   "title_on", "bg_review_on", "image_auto", "cwd_context", "no_purge", "home_inherited") {
        & python "$Here\h1_probe.py" --root $Root --out $Out --mutation $m *> (Join-Path $Out "probe-$m.log")
        $code = $LASTEXITCODE
        $rp = Join-Path $Out "report-$m.json"
        $failed = if (Test-Path $rp) { (Get-Content $rp -Raw | ConvertFrom-Json).failed } else { @("crash: " + ((Get-Content (Join-Path $Out "probe-$m.log") -Tail 3) -join " | ")) }
        $R.mutations[$m] = [ordered]@{ exit = $code; red = ($code -ne 0); failed = @($failed) }
        Save
    }
}

# ---------------------------------------------------------------- désinstallation
$S2 = Sentinels
function Paths([string]$text) {
    $clean = $text -replace "$([char]27)\[[0-9;]*m", ""
    return @([regex]::Matches($clean, "[A-Za-z]:\\[^\r\n]*") | ForEach-Object { $_.Value.Trim() })
}
$env:HERMES_HOME = $Root
$dry = & $launcher uninstall --full --dry-run 2>&1 | Out-String
$p1 = Paths $dry
$env:HERMES_HOME = $UserHome
$dryU = & $launcher uninstall --full --dry-run 2>&1 | Out-String
$p2 = Paths $dryU
$R.uninstall_dry_run = [ordered]@{
    bibo_home = [ordered]@{ paths = $p1; outside_root = @($p1 | Where-Object { -not $_.StartsWith($Root, "OrdinalIgnoreCase") }) }
    inherited_home = [ordered]@{ paths = $p2; outside_root = @($p2 | Where-Object { -not $_.StartsWith($Root, "OrdinalIgnoreCase") }) } }
$R.uninstall_dry_run.guard_allows_full = ($R.uninstall_dry_run.bibo_home.outside_root.Count -eq 0)
$R.uninstall_dry_run.guard_refuses_inherited = ($R.uninstall_dry_run.inherited_home.outside_root.Count -gt 0)
$env:HERMES_HOME = $Root
$sw = [Diagnostics.Stopwatch]::StartNew()
$u = & $launcher uninstall --full --yes 2>&1 | Out-String
$R.uninstall_full = [ordered]@{ exit = $LASTEXITCODE; seconds = [math]::Round($sw.Elapsed.TotalSeconds)
    tail = @(($u -replace "$([char]27)\[[0-9;]*m", "") -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 40)
    root_exists_after = (Test-Path $Root) }
if (Test-Path $Root) { $R.uninstall_full.root_left = Py @("$Here\h1_disk.py", "size", $Root) }
$env:HERMES_HOME = $UserHome
$S3 = Sentinels
$R.uninstall_sentinel_changes = DiffMap $S2 $S3
$R.probe_sentinel_changes = DiffMap $S1 $S2

# La désinstallation bornée de Bibo : ses processus, son dossier, son entrée de PATH.
Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($Root, "OrdinalIgnoreCase") } | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path $Root) { Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue }
$R.bounded_cleanup = [ordered]@{ root_exists = (Test-Path $Root); bibo_on_path = ((RawUserPath) -split ";" | Where-Object { $_.TrimEnd("\") -eq $bin }).Count }
Save

# ---------------------------------------------------------------- verdict du job
$ok = ($R.probe_main_exit -eq 0)
if ($Mutations) {
    $survivors = @($R.mutations.Keys | Where-Object { -not $R.mutations[$_].red })
    $R.mutation_survivors = $survivors
    $ok = $ok -and ($survivors.Count -eq 0)
}
Save
$md = @("## H1 $($R.os)", "", "- install: $($R.install.seconds) s, exit $($R.install.exit), root $([math]::Round($R.size_root.bytes/1MB)) Mo / $($R.size_root.files) fichiers",
        "- probe main: exit $($R.probe_main_exit), failed: $($R.probe_main.failed -join ', ')",
        "- Defender armé: RTP=$($R.defender_armed.RealTimeProtectionEnabled), EICAR=$($R.eicar_armed), détections=$(@($R.defender_scan.detections).Count) scan=$($R.defender_scan.result)",
        "- PATH: tête Bibo=$($R.path_head_is_bibo_bin), hermes après install=$($R.hermes_resolves_after_install), après parade=$($R.hermes_resolves_after_fix)",
        "- uninstall --full: exit $($R.uninstall_full.exit), dossier restant=$($R.uninstall_full.root_exists_after), sentinelles touchées: $(@($R.uninstall_sentinel_changes.Keys) -join ', ')")
if ($Mutations) { $md += "- mutations rouges: $(@($R.mutations.Keys | Where-Object { $R.mutations[$_].red }).Count)/$($R.mutations.Count), survivantes: $($R.mutation_survivors -join ', ')" }
$md -join "`n" | Out-File -Append -Encoding utf8 $env:GITHUB_STEP_SUMMARY
if (-not $ok) { exit 1 }
exit 0
