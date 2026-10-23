# Preuve H1 sous Windows (jetable) : Hermes installé comme Bibo le ferait, à côté d'un Hermes
# « de la personne » (sentinelles), mesuré, piloté par sa TUI gateway, purgé, désinstallé.
# Une phase par étape du job : chaque phase écrit son JSON dans h1out, téléversé aussitôt.
#   H1_DEFENDER = armed (temps réel pendant l'installation) | static (scan du dossier seulement)
#   H1_UNINSTALL = full (mutation : hermes uninstall --full) | bounded (la désinstallation de Bibo)
param([Parameter(Mandatory)][ValidateSet("setup", "install", "defender", "pathfix", "probe", "mutations", "uninstall")][string]$Phase)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Here = $PSScriptRoot
$Sha = $env:H1_HERMES_COMMIT
$Out = Join-Path $env:RUNNER_TEMP "h1out"
New-Item -ItemType Directory -Force $Out | Out-Null
$Root = Join-Path $env:LOCALAPPDATA "Bibo\hermes"
$Bin = Join-Path $Root "bin"
$UserHome = Join-Path $env:LOCALAPPDATA "hermes"
$AppDataHermes = Join-Path $env:APPDATA "Hermes"
$Xdg = @((Join-Path $HOME ".cache\hermes"), (Join-Path $HOME ".local\share\hermes"))
$Shortcuts = @((Join-Path ([Environment]::GetFolderPath('Programs')) 'Hermes.lnk'), (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Hermes.lnk'))
$env:H1_USER_HERMES_HOME = $UserHome
# H1_CONTAIN=1 : la parade de Bibo, tout ce que uv et node écrivent par défaut dans le profil va sous son dossier.
$Contain = @{}
if ($env:H1_CONTAIN -eq "1") {
    $Contain = @{ UV_PYTHON_INSTALL_DIR = (Join-Path $Root "uv-python"); UV_CACHE_DIR = (Join-Path $Root "cache\uv") }
    foreach ($k in $Contain.Keys) { [Environment]::SetEnvironmentVariable($k, $Contain[$k], "Process") }
}
$env:HERMES_HOME = $UserHome             # l'environnement hérité de la personne
"$Phase $(Get-Date -Format HH:mm:ss)" | Set-Content (Join-Path $Out "phase.txt")
$R = [ordered]@{ phase = $Phase; os = (Get-CimInstance Win32_OperatingSystem).Caption; build = [Environment]::OSVersion.Version.ToString() }

function Save { $R | ConvertTo-Json -Depth 14 | Set-Content (Join-Path $Out "$Phase.json") -Encoding UTF8 }
function Load([string]$name) { $f = Join-Path $Out "$name.json"; if (Test-Path $f) { Get-Content $f -Raw | ConvertFrom-Json } else { $null } }
function Py([string[]]$A) {
    $o = & python -I @A
    if ($LASTEXITCODE) { return @{ error = "python $($A[0]) $($A[1]) exit $LASTEXITCODE" } }
    return (($o -join "`n") | ConvertFrom-Json)
}
function Now { [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 }
function AsHash($o) {
    $h = [ordered]@{}
    if ($null -eq $o) { return $h }
    if ($o -is [System.Collections.IDictionary]) { foreach ($k in $o.Keys) { $h["$k"] = $o[$k] } }
    else { foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = $p.Value } }
    return $h
}
function DiffMap($a, $b) {
    $a = AsHash $a; $b = AsHash $b; $d = [ordered]@{}
    foreach ($n in @($a.Keys) + @($b.Keys) | Sort-Object -Unique) {
        $x = $a[$n]; $y = $b[$n]
        if ("$x" -ne "$y") { $d[$n] = [ordered]@{ before = $x; after = $y } }
    }
    return $d
}
function EnvKey { [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true) }
function RawUserPath { $k = EnvKey; try { $k.GetValue("Path", "", "DoNotExpandEnvironmentNames") } finally { $k.Close() } }
function UserPathKind { $k = EnvKey; try { if ($null -ne $k.GetValue("Path")) { "$($k.GetValueKind('Path'))" } else { "absent" } } finally { $k.Close() } }
function EnvSnapshot {
    $k = EnvKey; $h = [ordered]@{}
    try { foreach ($n in $k.GetValueNames()) { $h[$n] = "$($k.GetValueKind($n)):" + $k.GetValue($n, "", "DoNotExpandEnvironmentNames") } } finally { $k.Close() }
    return $h
}
function RunKeys {
    $h = [ordered]@{}
    foreach ($p in "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run", "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce") {
        $i = Get-ItemProperty $p -ErrorAction SilentlyContinue
        if ($i) { foreach ($q in $i.PSObject.Properties) { if ($q.Name -notlike "PS*") { $h["$p\$($q.Name)"] = "$($q.Value)" } } }
    }
    foreach ($k in Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue) { $h["uninstall:" + $k.PSChildName] = "$($k.GetValue('DisplayName'))" }
    return $h
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
    $files = Py @("$Here\h1_disk.py", "hashes", $UserHome, $AppDataHermes, $Xdg[0], $Xdg[1], $Shortcuts[0], $Shortcuts[1])
    $h = AsHash $files
    $h["HKCU:HERMES_HOME"] = [Environment]::GetEnvironmentVariable("HERMES_HOME", "User")
    $h["HKCU:HERMES_GIT_BASH_PATH"] = [Environment]::GetEnvironmentVariable("HERMES_GIT_BASH_PATH", "User")
    $h["task Hermes_Gateway"] = [bool](Get-ScheduledTask -TaskName "Hermes_Gateway" -ErrorAction SilentlyContinue)
    return $h
}
function Underroot([string]$p) { $p -and $p.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) }
function RootProcesses {
    @(Get-CimInstance Win32_Process | Where-Object { (Underroot $_.ExecutablePath) -or ($_.CommandLine -and $_.CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0) } |
        ForEach-Object { [ordered]@{ pid = $_.ProcessId; name = $_.Name; cmd = "$($_.CommandLine)".Substring(0, [math]::Min(200, "$($_.CommandLine)".Length)) } })
}
function KillRoot { foreach ($p in RootProcesses) { & taskkill.exe /T /F /PID $p.pid 2>&1 | Out-Null } }
# Une commande avec délai : sortie dans un fichier (un petit-enfant resté ouvert ne bloque rien).
function RunTimed([string]$exe, [string[]]$argv, [int]$seconds, [string]$label, [hashtable]$envs = @{}) {
    $saved = @{}
    foreach ($k in $envs.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k, "Process"); [Environment]::SetEnvironmentVariable($k, $envs[$k], "Process") }
    $o = Join-Path $Out "$label.out.log"; $e = Join-Path $Out "$label.err.log"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $quoted = @($argv | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } })
    $p = Start-Process -FilePath $exe -ArgumentList $quoted -WindowStyle Hidden -PassThru -RedirectStandardOutput $o -RedirectStandardError $e
    $null = $p.Handle
    $done = $p.WaitForExit($seconds * 1000)
    if (-not $done) { & taskkill.exe /T /F /PID $p.Id 2>&1 | Out-Null }
    $sw.Stop()
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], "Process") }
    $text = ((Get-Content $o -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content $e -Raw -ErrorAction SilentlyContinue)) -replace "$([char]27)\[[0-9;]*[A-Za-z]", ""
    return [ordered]@{ exit = $(if ($done) { $p.ExitCode } else { "timeout ${seconds}s" }); seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1); text = $text }
}
function Tail([string]$t, [int]$n) { @(($t -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -Last $n) }
function DefenderState {
    try {
        $s = Get-MpComputerStatus; $p = Get-MpPreference
        return [ordered]@{ AMServiceEnabled = $s.AMServiceEnabled; AntivirusEnabled = $s.AntivirusEnabled
            RealTimeProtectionEnabled = $s.RealTimeProtectionEnabled; BehaviorMonitorEnabled = $s.BehaviorMonitorEnabled
            IoavProtectionEnabled = $s.IoavProtectionEnabled; OnAccessProtectionEnabled = $s.OnAccessProtectionEnabled
            IsTamperProtected = $s.IsTamperProtected; AMRunningMode = $s.AMRunningMode
            EngineVersion = $s.AMEngineVersion; SignatureVersion = $s.AntivirusSignatureVersion; SignatureAge = $s.AntivirusSignatureAge
            ExclusionPath = @($p.ExclusionPath); ExclusionProcess = @($p.ExclusionProcess); ExclusionExtension = @($p.ExclusionExtension)
            DisableRealtimeMonitoring = $p.DisableRealtimeMonitoring; MAPSReporting = $p.MAPSReporting; SubmitSamplesConsent = $p.SubmitSamplesConsent }
    } catch { return @{ error = "$_" } }
}
function Detections($since) {
    @(Get-MpThreatDetection -ErrorAction SilentlyContinue | Where-Object { $_.InitialDetectionTime -ge $since } |
        ForEach-Object { $t = Get-MpThreat -ThreatID $_.ThreatID -ErrorAction SilentlyContinue
            [ordered]@{ threat = "$($t.ThreatName)"; resources = @($_.Resources); action_ok = $_.ActionSuccess; when = "$($_.InitialDetectionTime)" } })
}
function Eicar([string]$dir, [string]$label) {
    # Témoin : un fichier EICAR, assemblé ici pour que le script lui-même ne soit pas un échantillon.
    $parts = @('X5O!P%@AP[4\PZX54(P^)7CC)7}$', 'EICAR-STANDARD-ANTIVIRUS', '-TEST-FILE!$H+H*')
    New-Item -ItemType Directory -Force $dir | Out-Null
    $f = Join-Path $dir "h1-eicar-$label.com"
    try { [IO.File]::WriteAllText($f, ($parts -join "")) } catch { return "write blocked" }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path $f)) { return "quarantined" }
        try { $null = [IO.File]::ReadAllBytes($f) } catch { return "access denied (blocked)" }
        Start-Sleep -Milliseconds 500
    }
    Remove-Item $f -Force -ErrorAction SilentlyContinue
    return "NOT detected"
}
function Launcher {
    @((Join-Path $Root "hermes-agent\.hermes\bin\hermes.exe"), (Join-Path $Bin "hermes.exe"), (Join-Path $Bin "hermes.cmd")) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
function PathEntries([string]$raw) { @(($raw -split ";") | Where-Object { $_ }) }
function Finish([bool]$ok) { $R.ok = $ok; Save; if ($ok) { exit 0 } else { exit 1 } }

switch ($Phase) {
# ---------------------------------------------------------------- la personne a déjà Hermes ; Defender
"setup" {
    New-Item -ItemType Directory -Force (Join-Path $UserHome "bin"), $AppDataHermes, $Xdg[0], $Xdg[1] | Out-Null
    "# config Hermes de la personne (sentinelle)" | Set-Content (Join-Path $UserHome "config.yaml")
    "sentinelle" | Set-Content (Join-Path $UserHome "sentinel.txt")
    "@echo hermes de la personne" | Set-Content (Join-Path $UserHome "bin\hermes.cmd")
    "sentinelle" | Set-Content (Join-Path $AppDataHermes "sentinel.txt")
    foreach ($d in $Xdg) { "sentinelle" | Set-Content (Join-Path $d "sentinel.txt") }
    $k = EnvKey
    # PATH de la personne : REG_EXPAND_SZ avec une variable, comme sur un vrai poste.
    $k.SetValue("Path", "%LOCALAPPDATA%\hermes\bin;%USERPROFILE%\h1-outils;" + (RawUserPath), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    $k.SetValue("HERMES_HOME", $UserHome, [Microsoft.Win32.RegistryValueKind]::String)
    $k.SetValue("HERMES_GIT_BASH_PATH", "C:\fake\bash.exe", [Microsoft.Win32.RegistryValueKind]::String)
    $k.Close()
    Register-ScheduledTask -TaskName "Hermes_Gateway" -Action (New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c exit 0") -Force | Out-Null
    $R.sentinels = Sentinels; $R.env = EnvSnapshot; $R.runkeys = RunKeys
    $R.tasks = @(Get-ScheduledTask | ForEach-Object { $_.TaskPath + $_.TaskName }); $R.services = @(Get-Service | ForEach-Object Name)
    $R.path = [ordered]@{ raw = RawUserPath; kind = UserPathKind }
    $R.hermes_resolves = Resolve-Hermes
    $R.c_free_gb = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
    $R.defender_initial = DefenderState
    Save
    # Signatures à jour, sur les deux variantes
    $j = Start-Job { Update-MpSignature -ErrorAction Stop }
    if (Wait-Job $j -Timeout 300) { try { Receive-Job $j -ErrorAction Stop | Out-Null; $R.defender_update = "ok" } catch { $R.defender_update = "$_" } } else { Stop-Job $j; $R.defender_update = "timeout" }
    if ($env:H1_DEFENDER -eq "armed") {
        # Temps réel armé comme sur un PC, sans exclusion qui couvre le profil ; le runner garde les siennes.
        $p = Get-MpPreference
        $targets = @($Root, $env:TEMP, $env:LOCALAPPDATA, $env:APPDATA, $HOME)
        $cover = @(@($p.ExclusionPath) | Where-Object { $x = "$_".TrimEnd('\', '*'); $x -and @($targets | Where-Object { $_.StartsWith($x, [StringComparison]::OrdinalIgnoreCase) }).Count })
        $procs = @(@($p.ExclusionProcess) | Where-Object { (Split-Path "$_" -Leaf) -match '^(powershell|pwsh|python\d*|uv|uvx|node|npm|git|bash|cmd|hermes)(\.exe)?$' })
        $work = (Split-Path $env:GITHUB_WORKSPACE -Qualifier) + "\a"
        $keep = @($env:RUNNER_TOOL_CACHE, $work, $env:RUNNER_TEMP)
        $runner = Get-Process -Name Runner.Worker -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($runner -and $runner.Path) { $keep += (Split-Path (Split-Path $runner.Path)) }
        $keep = @($keep | Where-Object { $_ } | Sort-Object -Unique)
        try {
            if ($cover) { Remove-MpPreference -ExclusionPath $cover -ErrorAction Stop }
            if ($procs) { Remove-MpPreference -ExclusionProcess $procs -ErrorAction Stop }
            if ($cover) { Add-MpPreference -ExclusionPath $keep -ErrorAction Stop }
            Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false -DisableIOAVProtection $false `
                -DisableScriptScanning $false -MAPSReporting Advanced -SubmitSamplesConsent SendSafeSamples -ErrorAction Stop
        } catch { $R.defender_arm_error = "$_" }
        $R.defender_arm = [ordered]@{ removed_paths = $cover; removed_processes = $procs; runner_paths_kept = $(if ($cover) { $keep } else { @() }) }
    }
    $R.defender_ready = DefenderState
    $R.eicar = [ordered]@{ temp = Eicar $env:TEMP "temp"; localappdata = Eicar (Join-Path $env:LOCALAPPDATA "h1-eicar") "lad" }
    Remove-Item (Join-Path $env:LOCALAPPDATA "h1-eicar") -Recurse -Force -ErrorAction SilentlyContinue
    $R.t_defender = Now
    Finish $true
}
# ---------------------------------------------------------------- installation épinglée, silencieuse
"install" {
    $S = Load "setup"
    $inst = Join-Path $Out "install.ps1"
    # raw.githubusercontent.com rend parfois 429 aux runners : on réessaie, puis un miroir ; l'empreinte tranche.
    $R.download = @()
    foreach ($try in 1..6) {
        $u = if ($try -le 4) { "https://raw.githubusercontent.com/NousResearch/hermes-agent/$Sha/scripts/install.ps1" } else { "https://cdn.jsdelivr.net/gh/NousResearch/hermes-agent@$Sha/scripts/install.ps1" }
        try { Invoke-WebRequest $u -OutFile $inst -UseBasicParsing -ErrorAction Stop; $R.download += "ok $u"; break }
        catch { $R.download += "try $try $u : $($_.Exception.Message)"; Start-Sleep -Seconds (20 * $try) }
    }
    $got = (Get-FileHash $inst -Algorithm SHA256).Hash.ToLower()
    $R.install_ps1_sha256 = [ordered]@{ expected = $env:H1_INSTALL_PS1_SHA256; got = $got; ok = ($got -eq $env:H1_INSTALL_PS1_SHA256) }
    if (-not $R.install_ps1_sha256.ok) { Finish $false }
    $free0 = (Get-PSDrive C).Free
    $R.t0 = Now; Save
    $argv = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $inst, "-Commit", $Sha,
              "-NonInteractive", "-SkipBrowser", "-SkipComputerUse", "-HermesHome", $Root)
    $envs = @{}
    if ($env:H1_CONTAIN -eq "1") {
        $tmp = Join-Path $Root "tmp"; New-Item -ItemType Directory -Force $tmp | Out-Null
        $envs = @{ TEMP = $tmp; TMP = $tmp }
    }
    $R.contained = [ordered]@{ on = ($env:H1_CONTAIN -eq "1"); env = $Contain; temp = $envs.TEMP }
    $run = RunTimed "powershell.exe" $argv 3000 "install" $envs
    $R.install = [ordered]@{ seconds = $run.seconds; exit = $run.exit; c_drive_used_mb = [math]::Round(($free0 - (Get-PSDrive C).Free) / 1MB)
        tail = Tail $run.text 40 }
    $R.processes_left = RootProcesses
    Save
    Write-Host ((Tail $run.text 60) -join "`n")
    $R.size_root = Py @("$Here\h1_disk.py", "size", $Root)
    $R.written_outside_root = Py @("$Here\h1_disk.py", "newfiles", "$($R.t0)", "--root", $HOME, "--root", $env:ProgramData,
        "--exclude", $Root, "--exclude", $Out, "--exclude", (Join-Path $env:LOCALAPPDATA "Microsoft\Windows\WebCache"))
    $R.user_env_changes = DiffMap $S.env (EnvSnapshot)
    $R.sentinel_changes = DiffMap $S.sentinels (Sentinels)
    $R.runkey_changes = DiffMap $S.runkeys (RunKeys)
    $R.new_tasks = @(Get-ScheduledTask | ForEach-Object { $_.TaskPath + $_.TaskName } | Where-Object { @($S.tasks) -notcontains $_ })
    $R.new_services = @(Get-Service | ForEach-Object Name | Where-Object { @($S.services) -notcontains $_ })
    $R.path = [ordered]@{ raw = RawUserPath; kind = UserPathKind }
    $R.path_head_is_bibo_bin = ((PathEntries $R.path.raw)[0].TrimEnd("\") -eq $Bin)
    $R.path_kind_changed = ($R.path.kind -ne $S.path.kind)
    $R.person_entries_expanded = @(PathEntries $S.path.raw | Where-Object { $_ -like "*%*" -and (PathEntries $R.path.raw) -notcontains $_ })
    $R.hermes_resolves = Resolve-Hermes
    $R.launcher = Launcher
    $R.c_free_gb = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
    Finish ("$($run.exit)" -eq "0" -and [bool]$R.launcher)
}
# ---------------------------------------------------------------- Defender : verdict sur ce qui a été installé
"defender" {
    $S = Load "setup"
    $since = [DateTimeOffset]::FromUnixTimeMilliseconds([long]($S.t_defender * 1000)).LocalDateTime
    $R.mode = $env:H1_DEFENDER
    $R.detections_until_now = Detections $since
    $R.uv_exe = @(Get-ChildItem -Path $Root -Filter uv.exe -Recurse -ErrorAction SilentlyContinue | ForEach-Object { [ordered]@{ path = $_.FullName; bytes = $_.Length } })
    $removedStatic = @()
    if ($env:H1_DEFENDER -ne "armed") {
        # L'image exclut C:\ : un scan du dossier n'y verrait rien. On retire l'exclusion le temps du scan.
        $removedStatic = @(@((Get-MpPreference).ExclusionPath) | Where-Object { $x = "$_".TrimEnd('\', '*'); $x -and $Root.StartsWith($x, [StringComparison]::OrdinalIgnoreCase) })
        if ($removedStatic) { Remove-MpPreference -ExclusionPath $removedStatic -ErrorAction SilentlyContinue }
        # Témoin : un EICAR posé dans le dossier scanné doit être trouvé par le scan (temps réel coupé ici).
        $parts = @('X5O!P%@AP[4\PZX54(P^)7CC)7}$', 'EICAR-STANDARD-ANTIVIRUS', '-TEST-FILE!$H+H*')
        $witness = Join-Path $Root "h1-witness\eicar.com"
        New-Item -ItemType Directory -Force (Split-Path $witness) | Out-Null
        try { [IO.File]::WriteAllText($witness, ($parts -join "")); $R.scan_witness_written = $true } catch { $R.scan_witness_written = "$_" }
    }
    $R.removed_for_scan = $removedStatic
    $R.state_before_scan = DefenderState
    Save
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $j = Start-Job -ArgumentList $Root { param($r) Start-MpScan -ScanType CustomScan -ScanPath $r -ErrorAction Stop }
    if (Wait-Job $j -Timeout 1080) { try { Receive-Job $j -ErrorAction Stop | Out-Null; $scan = "done" } catch { $scan = "$_" } } else { Stop-Job $j; $scan = "timeout 18 min" }
    $R.scan = [ordered]@{ result = $scan; seconds = [math]::Round($sw.Elapsed.TotalSeconds) }
    $R.detections_total = Detections $since
    if ($env:H1_DEFENDER -ne "armed") {
        $R.scan_witness_found = @($R.detections_total | Where-Object { @($_.resources) -match 'h1-witness' }).Count -gt 0
        Remove-Item (Join-Path $Root "h1-witness") -Recurse -Force -ErrorAction SilentlyContinue
        if ($removedStatic) { Add-MpPreference -ExclusionPath $removedStatic -ErrorAction SilentlyContinue }
    }
    $R.eicar_end = Eicar $env:TEMP "end"
    $R.state_end = DefenderState
    if ($env:H1_DEFENDER -eq "armed") {
        # On rend la VM comme on l'a trouvée pour la suite du job (sonde, mutations).
        $init = $S.defender_initial
        try {
            $now = @((Get-MpPreference).ExclusionPath)
            $extra = @($now | Where-Object { @($init.ExclusionPath) -notcontains $_ })
            if ($extra) { Remove-MpPreference -ExclusionPath $extra -ErrorAction Stop }
            if ($S.defender_arm.removed_paths) { Add-MpPreference -ExclusionPath @($S.defender_arm.removed_paths) -ErrorAction Stop }
            if ($S.defender_arm.removed_processes) { Add-MpPreference -ExclusionProcess @($S.defender_arm.removed_processes) -ErrorAction Stop }
            Set-MpPreference -DisableRealtimeMonitoring ([bool]$init.DisableRealtimeMonitoring) -MAPSReporting $init.MAPSReporting -SubmitSamplesConsent $init.SubmitSamplesConsent -ErrorAction Stop
        } catch { $R.restore_error = "$_" }
        $R.state_restored = DefenderState
    }
    $R.launcher_still_there = [bool](Launcher)
    $R.uv_still_there = @($R.uv_exe | Where-Object { Test-Path $_.path }).Count
    Finish ([bool](Launcher))
}
# ---------------------------------------------------------------- la parade de Bibo : PATH rendu à l'identique
"pathfix" {
    $S = Load "setup"
    $cur = [ordered]@{ raw = RawUserPath; kind = UserPathKind }
    $kept = @(PathEntries $cur.raw | Where-Object { $_.TrimEnd("\") -ne $Bin })
    $expanded0 = @(PathEntries $S.path.raw | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_) })
    $expandedKept = @($kept | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_) })
    # Mutation calculée sans l'écrire : la parade de la spec (retirer l'entrée, garder le reste tel quel).
    $R.mutation_filter_only = [ordered]@{ raw_equal = (($kept -join ";") -eq $S.path.raw); kind_equal = ($cur.kind -eq $S.path.kind) }
    $R.mutation_filter_only.red = -not ($R.mutation_filter_only.raw_equal -and $R.mutation_filter_only.kind_equal)
    $k = EnvKey
    if (($expandedKept -join ";") -eq ($expanded0 -join ";")) {
        $k.SetValue("Path", $S.path.raw, [Microsoft.Win32.RegistryValueKind]$S.path.kind); $R.branch = "restored_exact"
    } else {
        $k.SetValue("Path", ($kept -join ";"), [Microsoft.Win32.RegistryValueKind]$cur.kind); $R.branch = "filtered (PATH changed by someone else meanwhile)"
    }
    $k.Close()
    $R.path_after = [ordered]@{ raw = RawUserPath; kind = UserPathKind }
    $R.path_identical_to_before = ($R.path_after.raw -eq $S.path.raw -and $R.path_after.kind -eq $S.path.kind)
    $R.hermes_resolves = Resolve-Hermes
    $l = Launcher
    if (-not $l) { $R.error = "no launcher"; Finish $false }
    $v = RunTimed $l @("--version") 300 "version" @{ HERMES_HOME = $Root }
    $R.full_path_call = [ordered]@{ exit = $v.exit; seconds = $v.seconds; first_line = (Tail $v.text 3) }
    $R.path_readded_after_version = @(PathEntries (RawUserPath) | Where-Object { $_.TrimEnd("\") -eq $Bin }).Count -gt 0
    Finish ($R.path_identical_to_before -and -not $R.path_readded_after_version)
}
# ---------------------------------------------------------------- la gateway, contre le faux fournisseur
"probe" {
    & python "$Here\h1_probe.py" --root $Root --out $Out --scan-home *> (Join-Path $Out "probe-main.log")
    $R.exit = $LASTEXITCODE
    Get-Content (Join-Path $Out "probe-main.log") -Tail 40 | Write-Host
    $rep = Load "report-main"
    $R.failed = if ($rep) { @($rep.failed) } else { @("no report") }
    $R.path_readded_by_hermes = @(PathEntries (RawUserPath) | Where-Object { $_.TrimEnd("\") -eq $Bin }).Count -gt 0
    $R.processes_left = RootProcesses
    Finish ($R.exit -eq 0 -and -not $R.path_readded_by_hermes)
}
"mutations" {
    $R.mutations = [ordered]@{}
    foreach ($m in "tools_default", "env_toolsets", "approvals_off", "approvals_smart", "yolo_env", "no_caps",
                   "bibo_approves", "title_on", "memory_on", "image_auto", "cwd_context", "no_purge", "home_inherited") {
        "mutations $m $(Get-Date -Format HH:mm:ss)" | Set-Content (Join-Path $Out "phase.txt")
        & python "$Here\h1_probe.py" --root $Root --out $Out --mutation $m *> (Join-Path $Out "probe-$m.log")
        $code = $LASTEXITCODE
        $rp = Load "report-$m"
        $R.mutations[$m] = [ordered]@{ exit = $code; failed = $(if ($rp) { @($rp.failed) } else { @("crash") })
            expected = $(if ($rp) { $rp.expected_red } else { $null }); killed_as_expected = [bool]($rp -and $rp.killed_as_expected) }
        Write-Host "mutation ${m}: exit $code, killed_as_expected=$($R.mutations[$m].killed_as_expected), failed=$($R.mutations[$m].failed -join ',')"
        Save
    }
    $R.survivors = @($R.mutations.Keys | Where-Object { -not $R.mutations[$_].killed_as_expected })
    Finish ($R.survivors.Count -eq 0)
}
# ---------------------------------------------------------------- désinstallation
"uninstall" {
    $S = Load "setup"; $I = Load "install"
    $l = Launcher
    $R.mode = $env:H1_UNINSTALL
    $S2 = Sentinels
    function Paths([string]$text) { @([regex]::Matches($text, "[A-Za-z]:\\[^\r\n]*") | ForEach-Object { $_.Value.Trim().TrimEnd(".", ":", ")") }) }
    if ($l) {
        $d1 = RunTimed $l @("uninstall", "--full", "--dry-run") 300 "uninstall-dry" @{ HERMES_HOME = $Root }
        $p1 = Paths $d1.text
        $R.dry_run_bibo = [ordered]@{ exit = $d1.exit; paths = $p1; outside_root = @($p1 | Where-Object { -not (Underroot $_) }) }
        $R.guard_allows_full = ($R.dry_run_bibo.outside_root.Count -eq 0)
        $S2b = $S2
        if ($env:H1_UNINSTALL -eq "full") {
            # Mutation : le hermes de Bibo lancé avec le HERMES_HOME de la personne, ne serait-ce qu'en --dry-run.
            $d2 = RunTimed $l @("uninstall", "--full", "--dry-run") 300 "uninstall-dry-inherited" @{ HERMES_HOME = $UserHome }
            $p2 = Paths $d2.text
            $R.dry_run_inherited = [ordered]@{ exit = $d2.exit; outside_root = @($p2 | Where-Object { -not (Underroot $_) }) }
            $S2b = Sentinels
            $R.sentinels_touched_by_inherited_dry_run = DiffMap $S2 $S2b
            $R.user_path_after_inherited_dry_run = RawUserPath
            $u = RunTimed $l @("uninstall", "--full", "--yes") 900 "uninstall-full" @{ HERMES_HOME = $Root }
            $R.full = [ordered]@{ exit = $u.exit; seconds = $u.seconds; tail = Tail $u.text 45; root_exists_after = (Test-Path $Root) }
            $R.sentinels_touched_by_full = DiffMap $S2b (Sentinels)
            $R.mutation_full_red = (@($R.sentinels_touched_by_full.Keys).Count -gt 0)
        }
    }
    # La désinstallation de Bibo : ses processus, son dossier, son entrée de PATH, rien d'autre.
    $sw = [Diagnostics.Stopwatch]::StartNew()
    KillRoot
    if (Test-Path $Root) { Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path $Root) { & cmd.exe /c rmdir /s /q "$Root" 2>&1 | Out-Null }
    $k = EnvKey
    $kind = UserPathKind
    $kept = @(PathEntries (RawUserPath) | Where-Object { -not (Underroot ([Environment]::ExpandEnvironmentVariables($_))) })
    if ($kind -ne "absent") { $k.SetValue("Path", ($kept -join ";"), [Microsoft.Win32.RegistryValueKind]$kind) }
    $k.Close()
    $R.bounded = [ordered]@{ seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1); root_exists = (Test-Path $Root); processes_left = RootProcesses }
    $R.final = [ordered]@{
        sentinel_changes_since_setup = DiffMap $S.sentinels (Sentinels)
        user_env_changes_since_setup = DiffMap $S.env (EnvSnapshot)
        runkey_changes_since_setup = DiffMap $S.runkeys (RunKeys)
        path_identical_to_setup = ((RawUserPath) -eq $S.path.raw -and (UserPathKind) -eq $S.path.kind)
        hermes_resolves = Resolve-Hermes
        new_tasks_left = @(Get-ScheduledTask | ForEach-Object { $_.TaskPath + $_.TaskName } | Where-Object { @($S.tasks) -notcontains $_ })
    }
    # Ce que l'installation a laissé hors du dossier et qui est toujours là
    $left = @(@($I.written_outside_root.paths) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
    $R.final.outside_left_total = $left.Count
    $hl = @($left | Where-Object { $_ -match '(?i)hermes|\\uv\\|\\uv-|node-compile-cache|npm-cache|\\npm\\|pnpm|node-gyp|electron' })
    $R.final.outside_left_hermes_count = $hl.Count
    $R.final.outside_left_hermes = @($hl | Select-Object -First 40)
    $R.final.outside_left_groups = @($left | ForEach-Object { ($_ -split '\\' | Select-Object -First 7) -join '\' } | Group-Object | Sort-Object Count -Descending |
        Select-Object -First 25 | ForEach-Object { "$($_.Count) $($_.Name)" })
    $ok = (-not $R.bounded.root_exists) -and $R.final.path_identical_to_setup
    if ($env:H1_UNINSTALL -eq "bounded") {
        $ok = $ok -and (@($R.final.sentinel_changes_since_setup.Keys).Count -eq 0) -and (@($R.final.user_env_changes_since_setup.Keys).Count -eq 0) -and ($hl.Count -eq 0)
    } else {
        # Job de mutation : le PATH et les sentinelles sont abîmés exprès ; on vérifie seulement que ça se voit.
        $ok = $R.mutation_full_red -and (-not $R.bounded.root_exists)
    }
    Finish $ok
}
}
