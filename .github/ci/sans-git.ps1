# A Windows PC as users have it, on a GitHub runner: no Git. The image installs Git for Windows with
# its own installer (runner-images, Install-Git.ps1: C:\Program Files\Git, four of its folders on the
# Machine PATH). Its uninstaller takes it away here, what the image added after it goes with its
# folder, then every Git folder leaves the Machine PATH, the user's and this process's (an installer
# started from this process inherits it). Then the proof that nothing finds Git any more, here and in
# a new process whose PATH is read again from the registry: Get-Command, where.exe, cmd.exe, Git's
# bash where Claude Code looks for it, Git's folders (install.ps1's Find-Git looks in two of them),
# App Paths, an uninstall entry. Git still found anywhere stops here, the proof saying where: the
# night would test nothing. winget, which brings Git, must be there (App Installer, which Windows 10
# and 11 have): windows-latest has it, its absence stops here too.
#
#   & .github/ci/sans-git.ps1 -Preuve <proof.json>              terrain-install.ps1 (terrain.yml, windows neuf)
#   powershell -File .github/ci/sans-git.ps1 -Preuve <proof.json>   terrain_app.py (app-windows), before the app
#
# terrain.py reads the proof again (scenarios installation and app, step git-absent-au-depart), probe
# by probe. The job's later steps keep the PATH the runner gave them: its Git folders are gone.
param([Parameter(Mandatory = $true)][string]$Preuve)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$debut = [Diagnostics.Stopwatch]::StartNew()

$racines = @(@("$env:ProgramFiles\Git", "${env:ProgramFiles(x86)}\Git", "$env:LOCALAPPDATA\Programs\Git") |
    Where-Object { $_ -and -not $_.StartsWith('\') })

function Test-SousGit([string]$p) {
    $p = "$p".Trim().Trim('"').TrimEnd('\')
    if (-not $p) { return $false }
    foreach ($r in $racines) {
        if ($p -ieq $r -or $p.StartsWith("$r\", [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

# A native command's stderr never stops the script (Windows PowerShell 5.1 makes it an error under Stop).
function Invoke-Natif([scriptblock]$c) {
    $ErrorActionPreference = 'Continue'
    & $c 2>$null
}

function Split-PathGit([string]$path) {
    $garde = @(); $git = @()
    foreach ($d in "$path" -split ';') {
        if (-not $d) { continue }
        if (Test-SousGit $d) { $git += $d } else { $garde += $d }
    }
    return @{ garde = ($garde -join ';'); git = $git }
}

$entrees = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Git_is1',
           'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Git_is1',
           'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Git_is1'

# --- Git for Windows, uninstalled by its own uninstaller
$desinstalle = @()
foreach ($cle in @($entrees | Where-Object { Test-Path -LiteralPath $_ })) {
    $e = Get-ItemProperty -LiteralPath $cle
    $unins = "$($e.UninstallString)".Trim().Trim('"')
    $dossier = "$($e.InstallLocation)".Trim().TrimEnd('\')
    $fait = [ordered]@{ entree = $cle; dossier = $dossier; version = "$($e.DisplayVersion)"; code = $null; secondes = $null; restes = 0 }
    # Nothing of Git still running (an ssh-agent, a credential helper) while it goes.
    Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and (Test-SousGit $_.Path) } catch { $false } } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    if ($unins -and (Test-Path -LiteralPath $unins)) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        # Inno Setup's uninstaller runs from a copy of itself in TEMP: -Wait waits for that copy too, and
        # unins000.exe is the last file it deletes.
        $p = Start-Process -FilePath $unins -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
        while ((Test-Path -LiteralPath $unins) -and $sw.Elapsed.TotalMinutes -lt 5) { Start-Sleep -Seconds 2 }
        $fait.code = $p.ExitCode
        $fait.secondes = [int]$sw.Elapsed.TotalSeconds
    }
    # What the image wrote there after the install (ssh_known_hosts, the system gitconfig) stays behind:
    # a PC that never had Git has no such folder.
    if ($dossier -and (Test-Path -LiteralPath $dossier)) {
        $fait.restes = @(Get-ChildItem -LiteralPath $dossier -Recurse -Force -File -ErrorAction SilentlyContinue).Count
        Remove-Item -LiteralPath $dossier -Recurse -Force
    }
    if (Test-Path -LiteralPath $cle) {
        $fait.entree_restee = $true
        Remove-Item -LiteralPath $cle -Recurse -Force
    }
    $desinstalle += $fait
}
# A Git folder without an uninstall entry (a portable copy): gone too.
$sans_entree = @()
foreach ($r in $racines) {
    if (Test-Path -LiteralPath $r) { Remove-Item -LiteralPath $r -Recurse -Force; $sans_entree += $r }
}
# Git's installer reads its last options there (the folder among them): a PC that never had Git has none.
$cles = @()
foreach ($k in 'HKLM:\SOFTWARE\GitForWindows', 'HKCU:\SOFTWARE\GitForWindows') {
    if (Test-Path -LiteralPath $k) { Remove-Item -LiteralPath $k -Recurse -Force; $cles += $k }
}

# --- the PATHs: the Machine's and the user's in the registry, then this process's
$path_retire = [ordered]@{}
foreach ($portee in 'Machine', 'User') {
    $s = Split-PathGit ([Environment]::GetEnvironmentVariable('Path', $portee))
    if ($s.git.Count) { [Environment]::SetEnvironmentVariable('Path', $s.garde, $portee) }
    $path_retire[$portee] = @($s.git)
}
$s = Split-PathGit $env:Path
$env:Path = $s.garde
$path_retire['processus'] = @($s.git)

# --- the proof: nothing finds Git, here
$bash = @(@('C:\Program Files\Git\bin\bash.exe', 'C:\Program Files (x86)\Git\bin\bash.exe') +
    @($racines | ForEach-Object { "$_\bin\bash.exe"; "$_\usr\bin\bash.exe" }) | Select-Object -Unique)
$app_paths = @()
foreach ($base in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths',
                  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths',
                  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths') {
    foreach ($k in @(Get-ChildItem -LiteralPath $base -ErrorAction SilentlyContinue)) {
        $cible = "$($k.GetValue(''))"
        if ($k.PSChildName -like 'git*.exe' -or (Test-SousGit $cible)) { $app_paths += "$($k.PSChildName) -> $cible" }
    }
}
$sondes = [ordered]@{
    get_command     = @(Get-Command git -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    where           = @(Invoke-Natif { where.exe git } | Where-Object { $_ })
    bash            = @($bash | Where-Object { Test-Path -LiteralPath $_ })
    dossiers        = @($racines | Where-Object { Test-Path -LiteralPath $_ })
    find_git        = @("$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe" | Where-Object { Test-Path -LiteralPath $_ })
    app_paths       = $app_paths
    desinstallation = @($entrees | Where-Object { Test-Path -LiteralPath $_ })
    path_machine    = @((Split-PathGit ([Environment]::GetEnvironmentVariable('Path', 'Machine'))).git)
    path_user       = @((Split-PathGit ([Environment]::GetEnvironmentVariable('Path', 'User'))).git)
}

# --- and in a new process, its PATH read again from the registry as Windows gives it to a new program
$enfant = @'
$ErrorActionPreference = 'Continue'
$w = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
[ordered]@{
    get_command = @(Get-Command git -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    where = @(where.exe git 2>$null | Where-Object { $_ })
    winget = $(if ($w) { $w.Source } else { $null })
} | ConvertTo-Json -Compress
'@
$avant = $env:Path
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
try {
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $code64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($enfant))
    $lu = (Invoke-Natif { & $ps -NoProfile -NonInteractive -EncodedCommand $code64 }) -join ''
    $nouveau = [ordered]@{ get_command = @(); where = @(); winget = $null; cmd_git_code = $null }
    try {
        $j = $lu.Substring($lu.IndexOf('{')) | ConvertFrom-Json
        $nouveau.get_command = @($j.get_command | Where-Object { $_ })
        $nouveau.where = @($j.where | Where-Object { $_ })
        $nouveau.winget = $j.winget
    } catch { $nouveau.illisible = "$lu".Substring(0, [Math]::Min(300, "$lu".Length)) }
    $null = Invoke-Natif { & "$env:SystemRoot\System32\cmd.exe" /d /c 'git --version' }
    $nouveau.cmd_git_code = [int]$LASTEXITCODE
} finally { $env:Path = $avant }

# --- winget, where this process and a new one find it
$w = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$wsource = if ($w) { $w.Source } elseif ($nouveau.winget) { $nouveau.winget } else { $null }
$winget = $null
if ($wsource) { $winget = [ordered]@{ chemin = $wsource; version = "$(Invoke-Natif { & $wsource --version })".Trim(); ici = [bool]$w; nouveau_processus = [bool]$nouveau.winget } }

$trouve = @()
foreach ($k in $sondes.Keys) { if (@($sondes[$k]).Count) { $trouve += "$k : $(@($sondes[$k]) -join ', ')" } }
if ($nouveau.get_command.Count -or $nouveau.where.Count) { $trouve += "nouveau processus : $(@($nouveau.get_command + $nouveau.where) -join ', ')" }
if ($nouveau.cmd_git_code -eq 0) { $trouve += 'nouveau processus : cmd.exe lance git --version' }
if ($nouveau.illisible) { $trouve += "nouveau processus illisible : $($nouveau.illisible)" }

$preuve_json = [ordered]@{
    absent = ($trouve.Count -eq 0)
    fin_epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    secondes = [int]$debut.Elapsed.TotalSeconds
    desinstalle = $desinstalle
    dossiers_sans_entree = $sans_entree
    cles_retirees = $cles
    path_retire = $path_retire
    sondes = $sondes
    nouveau_processus = $nouveau
    winget = $winget
    trouve = $trouve
}
New-Item -ItemType Directory -Path (Split-Path -Parent $Preuve) -Force | Out-Null
[IO.File]::WriteAllText($Preuve, ($preuve_json | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding $false))

foreach ($d in $desinstalle) { "Git for Windows $($d.version) ($($d.dossier)) : desinstalleur code $($d.code) en $($d.secondes) s, $($d.restes) fichier(s) restes retires" }
"PATH : $($path_retire.Machine.Count) dossier(s) Git retire(s) de Machine, $($path_retire.User.Count) de User, $($path_retire.processus.Count) de ce processus"
"winget : $(if ($winget) { "$($winget.chemin) $($winget.version)" } else { 'absent' })"
if ($trouve.Count) { throw "Git est encore trouvable sur cette VM : $($trouve -join ' ; ')" }
if (-not $winget) { throw "winget est absent de cette VM : l'installeur ne peut pas y prendre Git" }
"Git introuvable ici et dans un nouveau processus ($([int]$debut.Elapsed.TotalSeconds) s)"
