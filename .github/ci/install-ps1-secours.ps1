# install.ps1 when winget cannot bring Python or Git (absent, or its source broken, as on a GitHub VM on
# 2026-10-22: 0x8A15000F), against stand-ins: winget's source updated once then one more try, then the
# official installer at its pinned version, never run unless its SHA-256 is the pinned one, from a folder
# of the user's own, and the install log saying which way worked. Under pwsh on macOS, Linux and Windows;
# the real installers run in test.yml's job windows-winget-casse (install.ps1 whole, a broken winget).
#   pwsh -NoProfile -File .github/ci/install-ps1-secours.ps1
$ErrorActionPreference = "Stop"
$Kit = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$errs = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Kit "install.ps1"), [ref]$null, [ref]$errs)
if ($errs) { throw "install.ps1 does not parse: $errs" }
# Every function install.ps1 defines, and its winget codes and official installers as it sets them;
# none of its other statements runs.
foreach ($s in $ast.EndBlock.Statements) {
    if ($s -is [Management.Automation.Language.FunctionDefinitionAst] -or
        ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -clike '$WINGET_*') -or
        ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -clike '$OFFICIAL_*')) {
        . ([scriptblock]::Create($s.Extent.Text))
    }
}
foreach ($f in "Winget-Install", "Official-Install", "Install-Missing", "Get-WinArch") {
    if (-not (Get-Command $f -CommandType Function -ErrorAction SilentlyContinue)) { throw "install.ps1 has no function $f" }
}
$Pins = @{}
foreach ($k in $OFFICIAL_INSTALLERS.Keys) { $Pins[$k] = @{ x64 = @($OFFICIAL_INSTALLERS[$k].x64); arm64 = @($OFFICIAL_INSTALLERS[$k].arm64) } }
$Failures = 0
function Check([bool]$Cond, [string]$What) {
    if ($Cond) { Write-Output "ok    $What" } else { Write-Output "FAIL  $What"; $script:Failures++ }
}
function Said([scriptblock]$Do) { return (& $Do 6>&1 *>&1 | Out-String) }
function Sha256File([string]$p) {
    return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($p))) -replace '-', '').ToLowerInvariant()
}

$T = Join-Path ([IO.Path]::GetTempPath()) ("ps1-secours-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $T | Out-Null
$saved = @{}
foreach ($n in "TEMP", "TMP", "VOYAGE_API_KEY") { $saved[$n] = [Environment]::GetEnvironmentVariable($n, "Process") }

# The stand-ins: winget answers from a list of codes, the download serves a file or fails, the installer
# notes what it was started with (and the file it was given, as it was then), Find answers what it is told.
function winget {
    $script:WingetCalls += , ($args -join " ")
    $c = if ($script:WingetCodes.Count) { $script:WingetCodes[0] } else { 0 }
    $script:WingetCodes = @($script:WingetCodes | Select-Object -Skip 1)
    if ($c -eq -1978335217) { "Failed when opening source(s); try the 'source reset' command if the problem persists."; "0x8a15000f : Data required by the source is missing" }
    $global:LASTEXITCODE = $c
}
function Have($c) { if ($c -eq "winget") { return $script:WingetThere } return $false }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing)
    $script:Downloads += @($Uri)
    if (-not $script:Serve) { throw "no network" }
    Copy-Item -LiteralPath $script:Serve -Destination $OutFile
}
function Start-Process {
    [CmdletBinding()]
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$Wait, [switch]$PassThru)
    $script:Started += , ([ordered]@{ file = $FilePath; args = @($ArgumentList); temp = $env:TEMP; tmp = $env:TMP
                                      sha = (Sha256File $FilePath); voyage = "$env:VOYAGE_API_KEY" })
    return [pscustomobject]@{ ExitCode = $script:ExitCode }
}
function Get-WinArch { return $script:Arch }
function Refresh-Path { $script:Refreshed++ }

function New-Home {
    Remove-Item -LiteralPath (Join-Path $T "home") -Recurse -Force -ErrorAction SilentlyContinue
    $script:H = Join-Path $T "home"
    $script:LOG = Join-Path $H (Join-Path ".brain-in-a-box" "install.log")
    New-Item -ItemType Directory -Path (Split-Path -Parent $LOG) -Force | Out-Null
    Set-Content -LiteralPath $LOG -Value "" -Encoding utf8
    $script:LogOpen = $true; $script:WingetNotes = @()
    $script:WingetRepaired = $false; $script:WingetBroken = $false; $script:WingetThere = $true
    $script:WingetCalls = @(); $script:WingetCodes = @(); $script:Downloads = @(); $script:Serve = $null
    $script:Started = @(); $script:ExitCode = 0; $script:Arch = "x64"; $script:Refreshed = 0
    foreach ($k in $Pins.Keys) { $OFFICIAL_INSTALLERS[$k].x64 = @($Pins[$k].x64); $OFFICIAL_INSTALLERS[$k].arm64 = @($Pins[$k].arm64) }
    $env:TEMP = Join-Path $T "shared-temp"; $env:TMP = $env:TEMP
}
function Log { return [IO.File]::ReadAllText($LOG) }
function Leftovers { return @(Get-ChildItem -LiteralPath (Join-Path $H ".brain-in-a-box") -Directory -Filter "installeur-*" -ErrorAction SilentlyContinue) }
$Fake = Join-Path $T "fake-installer.exe"
Set-Content -LiteralPath $Fake -Value "not the pinned installer" -Encoding ascii
$FakeSha = Sha256File $Fake
$SourceBroken = -1978335217
$UserArgs = "install -e --id Git.Git --source winget --silent --accept-package-agreements --accept-source-agreements --scope user"

try {
    # The pins: the official releases, a version at least two weeks old, a SHA-256 for x64 and for ARM64,
    # silent installs for this user only.
    foreach ($k in "Git", "Python") {
        $o = $OFFICIAL_INSTALLERS[$k]
        $base = if ($k -eq "Git") { "https://github.com/git-for-windows/git/releases/download/v$($o.Version -replace '\.(\d+)$', '.windows.$1')/" } else { "https://www.python.org/ftp/python/$($o.Version)/" }
        $ok = $true
        foreach ($arch in "x64", "arm64") {
            $u, $sha = $o[$arch]
            if (-not ($u -is [string] -and $u.StartsWith($base) -and $u.Contains($o.Version) -and $u.EndsWith(".exe") -and $sha -cmatch '^[0-9a-f]{64}$')) { $ok = $false }
        }
        Check ($ok -and $o.x64[0] -ne $o.arm64[0] -and $o.x64[1] -ne $o.arm64[1]) "pins: $k $($o.Version), x64 and ARM64 from $base, each with its SHA-256"
    }
    Check ("$($OFFICIAL_INSTALLERS.Git.Args)" -ceq "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCANCEL /SP- /CURRENTUSER") "pins: Git's installer silent, no message box, no restart, for this user"
    Check ("$($OFFICIAL_INSTALLERS.Python.Args)" -ceq "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0") "pins: Python's installer quiet, for this user, on its PATH, no py launcher (all users: an admin window)"
    $ps1 = [IO.File]::ReadAllText((Join-Path $Kit "install.ps1"))
    Check ($ps1.Contains('$Py = Install-Missing "Python" "Python.Python.3.12" { Find-Python }') -and $ps1.Contains('$Git = Install-Missing "Git" "Git.Git" { Find-Git }') -and
           $ps1.Contains('$null = Winget-Install "Obsidian.Obsidian" "Obsidian"') -and -not $ps1.Contains('Install-Missing "Obsidian"')) `
          "install.ps1: Python and Git through winget then their official installer; Obsidian, optional, through winget only"

    # The official installer: one that is not the pinned one is downloaded, never run.
    New-Home; $Serve = $Fake
    $out = Said { $script:r = Official-Install "Git" }
    Check (-not $r -and $Started.Count -eq 0 -and "$Downloads" -ceq $Pins.Git.x64[0] -and
           $out -match "Git : le fichier telecharge ne correspond pas a l'empreinte SHA-256 epinglee, rien n'est lance" -and
           (Log).Contains("installeur officiel : empreinte SHA-256 $FakeSha, attendue $($Pins.Git.x64[1]) : rien n'est lance") -and (Leftovers).Count -eq 0) `
          "official installer: a replaced installer is downloaded from the release, never run, said on screen and in the log, its folder removed"
    # The pin changed (one character): the same file, still never run.
    New-Home; $Serve = $Fake; $bad = $FakeSha.Substring(0, 63) + $(if ($FakeSha.EndsWith("0")) { "1" } else { "0" })
    $OFFICIAL_INSTALLERS.Python.x64 = @($Pins.Python.x64[0], $bad)
    $out = Said { $script:r = Official-Install "Python" }
    Check (-not $r -and $Started.Count -eq 0 -and $out -match "rien n'est lance") "official installer: a pin one character off, nothing is run"

    # The pinned one runs: from a folder of the user's own (TEMP may be shared), with its silent arguments,
    # its TEMP in that folder, no Voyage key; the folder removed after, the session's TEMP back.
    New-Home; $Serve = $Fake; $OFFICIAL_INSTALLERS.Git.x64 = @($Pins.Git.x64[0], $FakeSha)
    $env:VOYAGE_API_KEY = "pa-canari-voyage"; $tempBefore = $env:TEMP
    $out = Said { $script:r = Official-Install "Git" }
    $s = if ($Started.Count) { $Started[0] } else { @{} }
    $own = Join-Path $H ".brain-in-a-box"
    Check ($r -and $Started.Count -eq 1 -and $s.sha -ceq $FakeSha -and $s.file -like (Join-Path $own "installeur-*") -and
           (Split-Path -Leaf $s.file) -ceq "Git-2.56.0.2-64-bit.exe" -and "$($s.args)" -ceq "$($OFFICIAL_INSTALLERS.Git.Args)" -and
           $s.temp -ceq (Join-Path (Split-Path -Parent $s.file) "tmp") -and $s.tmp -ceq $s.temp -and -not $s.voyage) `
          "official installer: the pinned one runs once, from ~\.brain-in-a-box\installeur-*, silent, its TEMP there, no Voyage key: $($s.file) $($s.temp)"
    Check ((Leftovers).Count -eq 0 -and $env:TEMP -ceq $tempBefore -and $env:TMP -ceq $tempBefore -and $env:VOYAGE_API_KEY -ceq "pa-canari-voyage" -and $Refreshed -ge 1 -and
           $out -match "Git 2\.56\.0\.2 : installeur officiel \(dans ton profil, sans droits admin\), verifie avant de tourner" -and
           (Log).Contains("> installeur officiel : Git 2.56.0.2 (x64) $($Pins.Git.x64[0])") -and (Log).Contains("installeur officiel : code 0")) `
          "official installer: its folder removed after, TEMP and the key back, PATH read again, the log says which and its code"
    foreach ($c in @(@(3010, $true), @(1603, $false))) {
        New-Home; $Serve = $Fake; $OFFICIAL_INSTALLERS.Git.x64 = @($Pins.Git.x64[0], $FakeSha); $ExitCode = $c[0]
        $null = Said { $script:r = Official-Install "Git" }
        Check ($r -eq $c[1] -and (Log).Contains("installeur officiel : code $($c[0])")) "official installer: code $($c[0]) is $(if ($c[1]) { 'done' } else { 'a failure' })"
    }
    # ARM64: its own installer; a 32-bit Windows: none, nothing downloaded; no network: said, nothing run.
    New-Home; $Serve = $Fake; $Arch = "arm64"; $OFFICIAL_INSTALLERS.Python.arm64 = @($Pins.Python.arm64[0], $FakeSha)
    $null = Said { $script:r = Official-Install "Python" }
    Check ($r -and "$Downloads" -ceq $Pins.Python.arm64[0] -and (Split-Path -Leaf $Started[0].file) -ceq "python-3.14.8-arm64.exe" -and
           "$($Started[0].args)" -ceq "$($OFFICIAL_INSTALLERS.Python.Args)") "official installer: on ARM64, the ARM64 one"
    New-Home; $Serve = $Fake; $Arch = ""
    $null = Said { $script:r = Official-Install "Git" }
    Check (-not $r -and $Downloads.Count -eq 0 -and $Started.Count -eq 0 -and (Log).Contains("aucun pour ce Windows 32 bits")) "official installer: none for a 32-bit Windows, nothing downloaded"
    New-Home
    $out = Said { $script:r = Official-Install "Git" }
    Check (-not $r -and $Started.Count -eq 0 -and $out -match "Git : telechargement impossible" -and (Log).Contains("telechargement impossible : no network") -and (Leftovers).Count -eq 0) `
          "official installer: no network, said, nothing run, its folder removed"

    # winget: its source broken (0x8A15000F), updated once without admin, one more try, then left alone.
    New-Home; $WingetCodes = @($SourceBroken, $SourceBroken, $SourceBroken)
    $out = Said { $script:r = Winget-Install "Git.Git" "Git" }
    Check (-not $r -and ($WingetCalls -join "|") -ceq "$UserArgs|source update --name winget|$UserArgs" -and $WingetBroken -and
           $out -match "winget : sa source ne s'ouvre pas, je la mets a jour" -and
           (Log).Contains("> winget $UserArgs`r`nwinget : code -1978335217") -and (Log).Contains("> winget source update --name winget`r`nwinget : code -1978335217")) `
          "winget: a broken source is updated once, the install tried once more, each in the log: $($WingetCalls -join ' | ')"
    $WingetCalls = @()
    $null = Said { $script:r = Winget-Install "Obsidian.Obsidian" "Obsidian" }
    Check (-not $r -and $WingetCalls.Count -eq 0 -and (Log).Contains("> winget : sa source est en panne, Obsidian non installe par winget")) "winget: its source still broken, not asked again"
    New-Home; $WingetCodes = @($SourceBroken, 0, 0)
    $null = Said { $script:r = Winget-Install "Git.Git" "Git" }
    Check ($r -and ($WingetCalls -join "|") -ceq "$UserArgs|source update --name winget|$UserArgs" -and -not $WingetBroken) "winget: the source updated, the install goes through"
    New-Home; $WingetCodes = @(-1978335216, 0)   # not the source (no installer for this scope): the package's default scope next
    $null = Said { $script:r = Winget-Install "Git.Git" "Git" }
    Check ($r -and $WingetCalls.Count -eq 2 -and $WingetCalls[1] -ceq ($UserArgs -replace ' --scope user$', '') -and -not $WingetRepaired) "winget: another failure, no source update, the default scope next"
    New-Home; $WingetRepaired = $true; $WingetCodes = @($SourceBroken)
    $null = Said { $script:r = Winget-Install "Git.Git" "Git" }
    Check (-not $r -and $WingetCalls.Count -eq 1 -and $WingetBroken) "winget: the source already updated once in this run, not twice"
    New-Home; $WingetThere = $false
    $null = Said { $script:r = Winget-Install "Git.Git" "Git" }
    Check (-not $r -and $WingetCalls.Count -eq 0 -and (Log).Contains("> winget absent : Git non installe par winget")) "winget absent: said in the log"

    # Both ways: winget broken then the official installer; winget alone; neither. The log says which way worked.
    New-Home; $WingetCodes = @($SourceBroken, $SourceBroken, $SourceBroken); $Serve = $Fake
    $OFFICIAL_INSTALLERS.Git.x64 = @($Pins.Git.x64[0], $FakeSha)
    $where = Join-Path $T "Programs/Git/cmd/git.exe"
    $got = $null; $null = Said { $script:got = Install-Missing "Git" "Git.Git" { if ($script:Started.Count) { $where } } }
    Check ($got -ceq $where -and $Started.Count -eq 1 -and (Log).Contains("> Git : $where (installeur officiel)")) "Git: winget's source broken, then its official installer; the log says so"
    New-Home; $WingetCodes = @(0)
    $got = $null; $null = Said { $script:got = Install-Missing "Python" "Python.Python.3.12" { if ($script:WingetCalls.Count) { "C:\py\python.exe" } } }
    Check ($got -ceq "C:\py\python.exe" -and $Downloads.Count -eq 0 -and (Log).Contains("> Python : C:\py\python.exe (winget)")) "Python: winget brings it, nothing else downloaded"
    New-Home; $WingetThere = $false; $Serve = $Fake
    $got = "x"; $null = Said { $script:got = Install-Missing "Git" "Git.Git" { $null } }
    Check (-not $got -and $Started.Count -eq 0 -and (Log).Contains("> Git : absent")) "Git: winget absent, the installer refused (not the pinned one): absent, said"
} finally {
    foreach ($n in $saved.Keys) { if ($null -eq $saved[$n]) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } else { Set-Item "Env:$n" $saved[$n] } }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output ""
Write-Output "$Failures failure(s)"
if ($Failures) { exit 1 }
