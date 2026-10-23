# install.ps1's stops, against stand-ins: each says its cause to the app (BIAB_ARRET=<cause>, the line
# before it stops, which the app turns into one sentence that says what to do), the cause of a missing
# Python is the one someone can act on (no winget, a company rule, a proxy, an antivirus, the network);
# winget's install for all accounts only on an administrator's account (a standard one would be asked a
# password it does not have); Bun installed even without Git; a Documents an antivirus closes sends a new
# brain to the profile's folder. Under pwsh on macOS, Linux and Windows; install.ps1 whole runs in test.yml.
#   pwsh -NoProfile -File .github/ci/install-ps1-arrets.ps1
$ErrorActionPreference = "Stop"
$Kit = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Win = [Environment]::OSVersion.Platform -eq "Win32NT"
$errs = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Kit "install.ps1"), [ref]$null, [ref]$errs)
if ($errs) { throw "install.ps1 does not parse: $errs" }
foreach ($s in $ast.EndBlock.Statements) {
    if ($s -is [Management.Automation.Language.FunctionDefinitionAst] -or
        ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -clike '$WINGET_*') -or
        ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -clike '$OFFICIAL_*')) {
        . ([scriptblock]::Create($s.Extent.Text))
    }
}
$ps1 = [IO.File]::ReadAllText((Join-Path $Kit "install.ps1"))
$Failures = 0
function Check([bool]$Cond, [string]$What) {
    if ($Cond) { Write-Output "ok    $What" } else { Write-Output "FAIL  $What"; $script:Failures++ }
}
function Said([scriptblock]$Do) { return (& $Do 6>&1 *>&1 | Out-String) }
# What it says, all of it, even when it stops: what stopped it in $Thrown.
function Try-Said([scriptblock]$Do) {
    $script:Thrown = ""
    return (& { try { & $Do } catch { $script:Thrown = "$_" } } 6>&1 *>&1 | Out-String)
}
function Has([string]$Name) { return [bool](Get-Command $Name -CommandType Function -ErrorAction SilentlyContinue) }

$T = Join-Path ([IO.Path]::GetTempPath()) ("ps1-arrets-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $T | Out-Null
$saved = @{}
foreach ($n in "TEMP", "TMP", "BRAIN_DIR") { $saved[$n] = [Environment]::GetEnvironmentVariable($n, "Process") }

# The stand-ins, as install-ps1-secours.ps1 has them: winget answers from a list of codes, the download serves a
# file or fails, the installer starts or Windows refuses it, the account is an administrator's or not.
function winget {
    $script:WingetCalls += , ($args -join " ")
    if ($script:WingetExe) { & $script:WingetExe @args; return }
    $c = if ($script:WingetCodes.Count) { $script:WingetCodes[0] } else { 0 }
    $script:WingetCodes = @($script:WingetCodes | Select-Object -Skip 1)
    $global:LASTEXITCODE = $c
}
function Get-Winget { if ($script:WingetThere) { return "winget" } }
function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing)
    if (-not $script:Serve) { throw "no network" }
    Copy-Item -LiteralPath $script:Serve -Destination $OutFile
}
function Start-Process {
    [CmdletBinding()]
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$Wait, [switch]$PassThru)
    # What Start-Process throws when Windows refuses to start a program: its reason inside.
    # Start-Process keeps only Windows' sentence, in Windows' language (StartProcessCommand); a reason in an inner exception too.
    if ($script:Refused -is [string]) { throw [InvalidOperationException]::new("This command cannot be run due to the error: $($script:Refused)") }
    if ($script:Refused) { throw [InvalidOperationException]::new("This command cannot be run", [ComponentModel.Win32Exception]::new($script:Refused)) }
    return [pscustomobject]@{ ExitCode = $script:ExitCode }
}
function Get-WinArch { return $script:Arch }
function Refresh-Path {}
function Test-Admin { return $script:Admin }
function Test-Proxy([string]$Url) { return $script:Proxy }

function New-Home {
    Remove-Item -LiteralPath (Join-Path $T "home") -Recurse -Force -ErrorAction SilentlyContinue
    $script:H = Join-Path $T "home"
    $script:LOG = Join-Path $H (Join-Path ".brain-in-a-box" "install.log")
    New-Item -ItemType Directory -Path (Split-Path -Parent $LOG) -Force | Out-Null
    Set-Content -LiteralPath $LOG -Value "" -Encoding utf8
    $script:LogOpen = $true; $script:WingetNotes = @()
    $script:WingetRepaired = $false; $script:WingetBroken = $false; $script:WingetThere = $true
    $script:WingetBlocked = $false; $script:WingetDown = $false; $script:WingetExe = $null; $script:OfficialArret = ""
    $script:WingetCalls = @(); $script:WingetCodes = @(); $script:Serve = $null; $script:Refused = 0
    $script:ExitCode = 0; $script:Arch = "x64"; $script:Admin = $false; $script:Proxy = $false
    $env:TEMP = Join-Path $T "temp"; $env:TMP = $env:TEMP
}
$Fake = Join-Path $T "fake-installer.exe"
Set-Content -LiteralPath $Fake -Value "the installer" -Encoding ascii
$FakeSha = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($Fake))) -replace '-', '').ToLowerInvariant()
$PythonPin = @($OFFICIAL_INSTALLERS.Python.x64)
$UserArgs = "install -e --id Python.Python.3.12 --source winget --silent --accept-package-agreements --accept-source-agreements --scope user"

# Python missing after winget and its official installer: the cause install.ps1 stops on.
function Arret-Python([scriptblock]$Setup) {
    New-Home
    . $Setup
    $null = Said { $script:got = Install-Missing "Python" "Python.Python.3.12" { $null } }
    return $(if (Has "Get-ArretPython") { Get-ArretPython } else { "(no Get-ArretPython)" })
}

try {
    # Fail says its cause, on a line of its own, before the line the user reads; then it stops.
    $out = Try-Said { Fail "Python introuvable." -Arret "winget_absent" }; $thrown = $Thrown
    $lines = @($out -split "\r?\n" | Where-Object { $_.Trim() })
    Check ($thrown -ceq "brain-in-a-box : installation arretee - Python introuvable." -and $lines.Count -ge 2 -and
           $lines[0] -ceq "BIAB_ARRET=winget_absent" -and $lines[1] -ceq "  [X]  Python introuvable.") `
          "Fail -Arret: BIAB_ARRET=<cause> first, then the sentence, then it stops: $($lines -join ' / ') ; $thrown"
    $out = Try-Said { Fail "autre chose" }
    Check ($Thrown -and $out -notmatch "BIAB_ARRET") "Fail without a cause: no BIAB_ARRET line"
    # Every stop install.ps1 makes names its cause, but the ISE's (a console, never the app).
    $fails = @([regex]::Matches($ps1, '(?m)\bFail "[^\r\n]*') | ForEach-Object { $_.Value } | Where-Object { $_ -notmatch '-Arret ' -and $_ -notmatch 'PowerShell ISE' })
    Check ($fails.Count -eq 0) "every Fail of install.ps1 names its cause (-Arret): $($fails -join ' | ')"
    $lm = $ps1.IndexOf('if ($ExecutionContext.SessionState.LanguageMode -ne "FullLanguage") {')
    $lmBlock = if ($lm -ge 0) { $ps1.Substring($lm, $ps1.IndexOf("`n}", $lm) - $lm) } else { "" }
    Check ($lmBlock.Contains('Write-Host "BIAB_ARRET=powershell_verrouille"') -and $lmBlock.IndexOf('BIAB_ARRET') -lt $lmBlock.IndexOf('throw')) `
          "a PowerShell the company locked (ConstrainedLanguage): BIAB_ARRET=powershell_verrouille before it stops"
    Check ($ps1.Contains('if (-not $Py) { Open-Log; Fail "Python introuvable.') -and $ps1.Contains('-Arret (Get-ArretPython) }')) `
          "install.ps1 stops on a missing Python with Get-ArretPython's cause"

    # Without winget, and no official installer for this Windows (32 bits): winget is what is missing.
    $a = Arret-Python { $script:WingetThere = $false; $script:Arch = "" }
    Check ($a -ceq "winget_absent" -and $WingetCalls.Count -eq 0) "no winget, no official installer: BIAB_ARRET=winget_absent ($a)"
    $a = Arret-Python { $script:WingetExe = $Fake + ".missing"; $script:Serve = $Fake; $OFFICIAL_INSTALLERS.Python.x64 = @($PythonPin[0], $FakeSha); $script:ExitCode = 1603 }
    Check ($a -ceq "winget_absent") "winget that does not start, the official installer failing: winget_absent ($a)"
    # winget blocked by the company: what to ask its IT service.
    $a = Arret-Python { $script:WingetCodes = @(-1978335174) }
    Check ($a -ceq "strategie_entreprise") "winget blocked by policy: strategie_entreprise ($a)"
    # No winget, the download fails: the network, or the company's proxy when the system has one.
    $a = Arret-Python { $script:WingetThere = $false }
    Check ($a -ceq "reseau") "no winget, python.org unreachable, no proxy: reseau ($a)"
    $a = Arret-Python { $script:WingetThere = $false; $script:Proxy = $true }
    Check ($a -ceq "proxy_entreprise") "no winget, python.org unreachable behind a proxy: proxy_entreprise ($a)"
    # Something else than the pinned installer served (a Wi-Fi portal's page, a proxy's filter): never run.
    $a = Arret-Python { $script:WingetThere = $false; $script:Serve = $Fake; $script:Proxy = $true }
    Check ($a -ceq "proxy_entreprise") "a page instead of the installer, behind a proxy: proxy_entreprise ($a)"
    # Windows refuses to start the installer: an antivirus (225, 226), a policy (1260); or the installer refuses (1625).
    foreach ($c in @(@(225, "antivirus_bloque"), @(226, "antivirus_bloque"), @(1260, "strategie_entreprise"),
                     @("Operation did not complete successfully because the file contains a virus or potentially unwanted software.", "antivirus_bloque"),
                     @("L'operation n'a pas abouti, car le fichier contient un virus ou un logiciel potentiellement indesirable.", "antivirus_bloque"),
                     @("This program is blocked by group policy. For more information, contact your system administrator.", "strategie_entreprise"))) {
        $a = Arret-Python { $script:WingetThere = $false; $script:Serve = $Fake; $OFFICIAL_INSTALLERS.Python.x64 = @($PythonPin[0], $FakeSha); $script:Refused = $c[0] }
        Check ($a -ceq $c[1]) "the installer refused by Windows ($($c[0])): $($c[1]) ($a)"
    }
    $a = Arret-Python { $script:WingetThere = $false; $script:Serve = $Fake; $OFFICIAL_INSTALLERS.Python.x64 = @($PythonPin[0], $FakeSha); $script:ExitCode = 1625 }
    Check ($a -ceq "strategie_entreprise") "the installer forbidden by a policy (1625): strategie_entreprise ($a)"
    # winget there, Python not installed by it nor by its installer: Python itself.
    $a = Arret-Python { $script:WingetCodes = @(-1978335216); $script:Serve = $Fake; $OFFICIAL_INSTALLERS.Python.x64 = @($PythonPin[0], $FakeSha); $script:ExitCode = 1603 }
    Check ($a -ceq "python_absent") "winget and the official installer both failed: python_absent ($a)"
    $OFFICIAL_INSTALLERS.Python.x64 = $PythonPin

    # winget's package installer for all accounts: only on an administrator's account.
    New-Home; $WingetCodes = @(-1978335216, -1978335216)
    $null = Said { $script:r = Winget-Install "Python.Python.3.12" "Python" }
    Check ($WingetCalls.Count -eq 1 -and $WingetCalls[0] -ceq $UserArgs) "a standard account: winget for this user only, never a password asked: $($WingetCalls -join ' | ')"
    New-Home; $Admin = $true; $WingetCodes = @(-1978335216, 0)
    $null = Said { $script:r = Winget-Install "Python.Python.3.12" "Python" }
    Check ($r -and $WingetCalls.Count -eq 2 -and $WingetCalls[1] -ceq ($UserArgs -replace ' --scope user$', '')) "an administrator's account: the package's own installer next: $($WingetCalls -join ' | ')"
    # The real check, on this runner: an administrator on Windows (GitHub's), nobody elsewhere.
    $real = @($ast.EndBlock.Statements | Where-Object { $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq "Test-Admin" })
    $isAdmin = if ($real.Count) { & $real[0].Body.GetScriptBlock() } else { "(no Test-Admin)" }
    Check ($isAdmin -eq $Win) "Test-Admin on this runner: $isAdmin"

    # Without Git: Bun is installed all the same, gbrain waits for Git.
    New-Home; $savedPath = @{ PATH = $env:PATH; Path = [Environment]::GetEnvironmentVariable("Path") }
    $BUNBIN = Join-Path $T "bun-bin"; $BUN_VERSION = "1.4.3"; $Git = $null; $script:BunThere = $false; $script:Logged = @()
    function Get-BunVersion([string]$Bun) { if ($script:BunThere) { return [version]"1.4.3" } }
    function Invoke-Logged([string]$Exe, [string[]]$ArgList, [string]$Dir = "") {
        $script:Logged += , ($ArgList -join " ")
        if (($ArgList -join " ") -match 'bun\.sh/install\.ps1') { $script:BunThere = $true }
        $global:LASTEXITCODE = 0
        return 0
    }
    $out = Said { $script:r = Install-Gbrain }
    Check (-not $r -and $BunThere -and $Logged.Count -eq 1 -and $out -match "\[OK\] bun 1\.4\.3" -and $out -match "gbrain a besoin de Git") `
          "no Git: Bun installed anyway, gbrain waits for Git: $($Logged -join ' | ') ; $($out.Trim() -replace '\r?\n', ' / ')"
    $env:PATH = $savedPath.PATH
    if (-not $Win) { if ($null -eq $savedPath.Path) { Remove-Item Env:Path -ErrorAction SilentlyContinue } else { $env:Path = $savedPath.Path } }

    # A new brain where the kit chose Documents, closed by an antivirus: in the profile's folder instead; a
    # folder the user chose stays theirs, and the stop says why.
    # Windows only: the block's paths are written with backslashes, as install.ps1 runs on Windows alone.
    if ($Win) {
        $start = $ps1.IndexOf('$GuardMoved = $false'); $end = $ps1.IndexOf('# 3. Hooks')
        $vault = [scriptblock]::Create($ps1.Substring($start, $end - $start))
        function Test-FolderGuard { return $false }
        $REPO = Join-Path $T "kit"
        New-Item -ItemType Directory -Path (Join-Path $REPO (Join-Path "vault-skeleton" "Profile")) -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $REPO (Join-Path "vault-skeleton" "CLAUDE.md")) -Value "# brain" -Encoding ascii
        function Close([string]$Dir) {
            New-Item -ItemType Directory -Path $Dir -Force | Out-Null
            $null = & icacls.exe $Dir /deny "$($env:USERNAME):(OI)(CI)(W,AD,WD)"
        }
        function Open([string]$Dir) {
            $null = & icacls.exe $Dir /remove:d $env:USERNAME
        }
        New-Home; Remove-Item Env:BRAIN_DIR -ErrorAction SilentlyContinue
        $docs = Join-Path $H "Documents"; Close $docs
        $BrainDir = ""; $script:Vault = ""
        $out = Try-Said { . $vault; $script:Vault = $BrainDir }; $thrown = $Thrown
        Open $docs
        Check (-not $thrown -and $Vault -ceq "$H\Brain" -and (Test-Path -LiteralPath (Join-Path $H (Join-Path "Brain" "CLAUDE.md"))) -and
               -not (Test-Path -LiteralPath (Join-Path $docs "Brain")) -and $out -match "Documents est protege") `
              "Documents closed by an antivirus: the new brain goes to the profile's folder: $Vault ; $thrown ; $($out.Trim() -replace '\r?\n', ' / ')"
        New-Home; Close (Join-Path $H "closed"); $env:BRAIN_DIR = Join-Path (Join-Path $H "closed") "Brain"
        $BrainDir = ""
        $out = Try-Said { . $vault }; $thrown = $Thrown
        Open (Join-Path $H "closed"); Remove-Item Env:BRAIN_DIR
        Check ($thrown -match "installation arretee" -and $out -match "(?m)^BIAB_ARRET=acces_documents_refuse\r?$" -and -not (Test-Path -LiteralPath (Join-Path $H "Brain"))) `
              "a folder the user chose, closed: BIAB_ARRET=acces_documents_refuse, nothing put elsewhere: $thrown"
    } else { Write-Output "skip  a Documents an antivirus closes (Windows only)" }

    # The machine stays awake while install.ps1 runs, and lets go at the end and when it stops.
    $first = $ps1.IndexOf("`nSet-Awake `$true"); $home1 = $ps1.IndexOf("`n`$H = `$env:USERPROFILE")
    Check ((Has "Set-Awake") -and $first -gt 0 -and $first -lt $home1 -and $ps1.TrimEnd().EndsWith("Set-Awake `$false") -and
           ${function:Fail}.ToString().Contains('Set-Awake $false')) "no sleep during the install: Set-Awake on first, off at the end and in Fail"
    $null = Said { Set-Awake $true; Set-Awake $false }
    Check $true "Set-Awake does not throw here ($(if ($Win) { 'Windows' } else { 'not Windows: nothing to do' }))"
    # The reflection at 12:00 and 23:00 opens no black window (pythonw), as the nightly at 04:00.
    Check ($ps1.Contains('Register-BrainTask "BrainReflection" $Pyw ') -and $ps1.Contains('Register-BrainTask "BrainNightly" $Pyw ')) "BrainReflection runs pythonw.exe"
} finally {
    foreach ($n in $saved.Keys) { if ($null -eq $saved[$n]) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } else { Set-Item "Env:$n" $saved[$n] } }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output ""
Write-Output "$Failures failure(s)"
if ($Failures) { exit 1 }
