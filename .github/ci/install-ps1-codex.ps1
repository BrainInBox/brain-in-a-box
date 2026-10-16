# install.ps1's Codex functions (its block "# >>> codex") against stand-ins, as test-hooks.sh, section
# 15bis, runs install.sh's: Codex's installer (the pinned one only, with the right environment),
# hooks.json and its approval in config.toml, the global AGENTS.md, no gbq rule, the vault's
# AGENTS.md, the question. What they write, what they say, that nothing of the user's is lost.
# Under pwsh on macOS and Linux, and on Windows (where the stand-in codex.exe is compiled). The real
# Codex runs them in test.yml: the Windows job (install.ps1 whole) and codex-mock (Windows).
#   pwsh -NoProfile -File .github/ci/install-ps1-codex.ps1
$ErrorActionPreference = "Stop"
$Kit = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Win = [Environment]::OSVersion.Platform -eq "Win32NT"
$errs = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Kit "install.ps1"), [ref]$null, [ref]$errs)
if ($errs) { throw "install.ps1 does not parse: $errs" }
# Every function install.ps1 defines, and its Codex pins as it sets them; none of its other statements runs.
foreach ($s in $ast.EndBlock.Statements) {
    if ($s -is [Management.Automation.Language.FunctionDefinitionAst] -or
        ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -like '$CODEX_*')) {
        . ([scriptblock]::Create($s.Extent.Text))
    }
}
$PinnedSha = $CODEX_INSTALLER_SHA256
$Failures = 0
function Check([bool]$Cond, [string]$What) {
    if ($Cond) { Write-Output "ok    $What" } else { Write-Output "FAIL  $What"; $script:Failures++ }
}
function Said([scriptblock]$Do) { return (& $Do 6>&1 *>&1 | Out-String) }

$T = Join-Path ([IO.Path]::GetTempPath()) ("ps1-codex-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $T | Out-Null
$Py = if ($Win) { (Get-Command python -CommandType Application | Select-Object -First 1).Source } else { (Get-Command python3 -CommandType Application | Select-Object -First 1).Source }
if (-not $Py -or $Py -like '*\WindowsApps\*') { throw "no Python on the PATH (only the Store stub?): $Py" }
# Its real path (no link, no short name): Codex names hooks.json by it, and wire.py approves both when they differ.
$T = "$(& $Py -I -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' $T)".Trim()
$saved = @{}
foreach ($n in "HOME", "USERPROFILE", "LOCALAPPDATA", "VOYAGE_API_KEY", "CODEX_HOME", "CODEX_INSTALL_DIR", "BIAB_AGENTS", "BIAB_PREFERE",
               "FAKE_CODEX_CALLS", "FAKE_CODEX_VERSION", "FAKE_FEATURES_RC", "FAKE_FEATURES_AFTER") {
    $saved[$n] = [Environment]::GetEnvironmentVariable($n, "Process")
}

# The stand-in codex: it notes its calls, says its version, and features list answers what it is told.
$FakeCodex = Join-Path $T "fake-codex.exe"
if ($Win) {
    $cs = Join-Path $T "fake-codex.cs"
    Set-Content -LiteralPath $cs -Encoding ascii -Value @'
using System; using System.IO;
class C { static int Main(string[] a) {
    string calls = Environment.GetEnvironmentVariable("FAKE_CODEX_CALLS");
    if (calls != null) File.AppendAllText(calls, "codex " + string.Join(" ", a) + " CODEX_HOME=" + Environment.GetEnvironmentVariable("CODEX_HOME") + "\n");
    if (a.Length > 0 && a[0] == "--version") { Console.WriteLine("codex-cli " + (Environment.GetEnvironmentVariable("FAKE_CODEX_VERSION") ?? "0.162.1")); return 0; }
    if (a.Length > 0 && a[0] == "features") {
        string after = Environment.GetEnvironmentVariable("FAKE_FEATURES_AFTER");
        if (!string.IsNullOrEmpty(after)) { if (File.Exists(after)) return 1; File.WriteAllText(after, "x"); }
        string rc = Environment.GetEnvironmentVariable("FAKE_FEATURES_RC");
        return string.IsNullOrEmpty(rc) ? 0 : int.Parse(rc);
    }
    return 0;
} }
'@
    & "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo "/out:$FakeCodex" $cs | Out-Null
    if ($LASTEXITCODE) { throw "the stand-in codex.exe did not compile" }
} else {
    Set-Content -LiteralPath $FakeCodex -Encoding ascii -Value @'
#!/bin/sh
[ -n "$FAKE_CODEX_CALLS" ] && echo "codex $* CODEX_HOME=$CODEX_HOME" >> "$FAKE_CODEX_CALLS"
case "$1" in
  --version) echo "codex-cli ${FAKE_CODEX_VERSION:-0.162.1}";;
  features)
    if [ -n "$FAKE_FEATURES_AFTER" ]; then [ -e "$FAKE_FEATURES_AFTER" ] && exit 1; : > "$FAKE_FEATURES_AFTER"; fi
    exit "${FAKE_FEATURES_RC:-0}";;
esac
'@
    chmod 755 $FakeCodex
}
$Calls = Join-Path $T "calls"
$env:FAKE_CODEX_CALLS = $Calls
# The stand-in of Codex's installer: it notes its environment, puts codex.exe where the official one goes.
$Installer = Join-Path $T "installer.ps1"
Set-Content -LiteralPath $Installer -Encoding ascii -Value @"
Get-ChildItem Env: | ForEach-Object { "`$(`$_.Name)=`$(`$_.Value)" } | Set-Content -LiteralPath '$T/installer-env' -Encoding utf8
`$bin = Join-Path `$env:LOCALAPPDATA 'Programs/OpenAI/Codex/bin'
New-Item -ItemType Directory -Path `$bin -Force | Out-Null
Copy-Item -LiteralPath '$FakeCodex' -Destination (Join-Path `$bin 'codex.exe')
Write-Output 'PATH updated for future PowerShell sessions.'
"@
$InstallerSha = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($Installer))) -replace '-', '').ToLowerInvariant()
function Invoke-WebRequest {
    param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing)
    $script:Downloads += @($Uri)
    if (-not $script:Serve) { throw "no network" }
    Copy-Item -LiteralPath $script:Serve -Destination $OutFile
}
function Test-Interactive { return $script:Interactive }
function Read-Host {
    param([string]$Prompt)
    $script:Asked += @($Prompt)
    $a = if ($script:Answers.Count) { $script:Answers[0] } else { "" }
    $script:Answers = @($script:Answers | Select-Object -Skip 1)
    return $a
}

# A home of its own, as install.ps1 sets one up before its Codex section runs.
function New-Home([string]$RepoDir = $Kit) {
    foreach ($n in "home", "localappdata", "after") { Remove-Item -LiteralPath (Join-Path $T $n) -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $Calls, (Join-Path $T "installer-env") -Force -ErrorAction SilentlyContinue
    $script:H = Join-Path $T "home"
    $script:HOOKS = Join-Path $H (Join-Path ".claude" (Join-Path "hooks" "brain"))
    $script:BIN = Join-Path $H (Join-Path ".local" "bin")
    $script:CodexDir = Join-Path $H ".codex"
    $script:LOG = Join-Path $H (Join-Path ".brain-in-a-box" "install.log")
    $script:MANIFEST = Join-Path $H (Join-Path ".brain-in-a-box" "installed.json")
    $script:BACKUP = Join-Path $H (Join-Path ".brain-in-a-box" (Join-Path "backups" "test"))
    $script:BackedUp = @()
    $script:AgentsFile = Join-Path $H (Join-Path ".brain-in-a-box" "agents.json")
    $script:REPO = $RepoDir
    $script:Downloads = @(); $script:Serve = $null; $script:Asked = @(); $script:Answers = @(); $script:Interactive = $false
    $script:Agents = ""; $script:Prefere = ""
    $script:CODEX_INSTALLER_SHA256 = $PinnedSha
    foreach ($d in $HOOKS, $BIN, $CodexDir, (Split-Path -Parent $LOG)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    Set-Content -LiteralPath $LOG -Value "" -Encoding utf8
    $env:HOME = $H; $env:USERPROFILE = $H
    $env:LOCALAPPDATA = Join-Path $T "localappdata"
    foreach ($n in "CODEX_HOME", "CODEX_INSTALL_DIR", "BIAB_AGENTS", "BIAB_PREFERE", "FAKE_CODEX_VERSION", "FAKE_FEATURES_RC", "FAKE_FEATURES_AFTER") {
        Remove-Item "Env:$n" -ErrorAction SilentlyContinue
    }
}
function Put-Codex {   # what the official installer leaves: codex.exe where Find-Codex looks first
    $bin = Join-Path $env:LOCALAPPDATA (Join-Path "Programs" (Join-Path "OpenAI" (Join-Path "Codex" "bin")))
    New-Item -ItemType Directory -Path $bin -Force | Out-Null
    Copy-Item -LiteralPath $FakeCodex -Destination (Join-Path $bin "codex.exe")
}
function Calls { if (Test-Path -LiteralPath $Calls) { $c = @(Get-Content -LiteralPath $Calls); Remove-Item -LiteralPath $Calls; return ,$c } return ,@() }
function Baks([string]$Name) { return @(Get-ChildItem -LiteralPath $CodexDir -Filter "$Name.bak-*" -Force).Count }
function Read-Text([string]$p) { return [IO.File]::ReadAllText($p) }
function Same([string]$a, [string]$b) { return [Convert]::ToBase64String([IO.File]::ReadAllBytes($a)) -eq [Convert]::ToBase64String([IO.File]::ReadAllBytes($b)) }
function Sha256Hex([string]$s) {
    return ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($s))) -replace '-', '').ToLowerInvariant()
}
function Hook-Command([string]$Name) {   # the PowerShell form Codex runs on Windows, built by hand
    $q = { param($s) "'" + $s.Replace("'", "''") + "'" }
    $hook = if ($Win) { Join-Path $HOOKS $Name } else { "$HOOKS\$Name" }   # wire.py joins as Windows does
    return "& $(& $q $Py) $(& $q $hook) --agent codex"
}
function Trust([string]$Event, [string]$Name) {   # Codex's hash of a hook: normalized, keys sorted, no spaces
    $c = (Hook-Command $Name).Replace('\', '\\').Replace('"', '\"')
    return "sha256:" + (Sha256Hex ('{"event_name":"' + $Event + '","hooks":[{"async":false,"command":"' + $c + '","timeout":30,"type":"command"}]}'))
}

try {
    # The pins: the release CI tests, the same Codex as install.sh's.
    $sh = Read-Text (Join-Path $Kit "install.sh")
    Check ($CODEX_INSTALLER_URL -ceq "https://github.com/openai/codex/releases/download/rust-v$CODEX_VERSION/install.ps1" -and
           $PinnedSha -cmatch '^[0-9a-f]{64}$' -and $sh.Contains("CODEX_VERSION=$CODEX_VERSION`n")) `
          "codex installer: the release's install.ps1 of the version install.sh pins too ($CODEX_VERSION), its SHA-256 pinned"

    # Its installer: the pinned one only, never run when it does not match, with the right environment.
    New-Home; $Serve = $Installer
    $out = Said { Install-Codex }
    Check ($out -match "ne correspond pas a l'empreinte SHA-256 epinglee, rien n'est lance" -and -not (Test-Path (Join-Path $T "installer-env")) -and
           -not (Find-Codex) -and "$Downloads" -ceq $CODEX_INSTALLER_URL) "codex installer: one that is not the pinned one is downloaded from the release, never run"
    New-Home; $Serve = $Installer; $CODEX_INSTALLER_SHA256 = $InstallerSha
    $env:VOYAGE_API_KEY = "pa-canari-voyage"; $env:CODEX_INSTALL_DIR = (Join-Path $T "trap"); $env:CODEX_HOME = (Join-Path $T "other-home")
    $gnu = Join-Path $T "git-usr-bin"   # where Git's Unix tools, GNU tar among them, sit first on some PATHs
    $pathPut = $env:PATH; if ($Win) { $env:PATH = "$gnu;$env:PATH" }
    $pathBefore = "$env:Path|$env:PATH"
    $out = Said { Install-Codex }
    $ie = if (Test-Path (Join-Path $T "installer-env")) { @(Get-Content (Join-Path $T "installer-env")) } else { @() }
    Check ($ie -ccontains "CODEX_RELEASE=$CODEX_VERSION" -and $ie -ccontains "CODEX_NON_INTERACTIVE=1" -and $ie -ccontains "CODEX_HOME=$CodexDir" -and
           -not ($ie -match '^VOYAGE_API_KEY=') -and -not ($ie -match '^CODEX_INSTALL_DIR=') -and $out -match "\[OK\] Codex $([regex]::Escape($CODEX_VERSION)) installe" -and
           $out -match "son installeur a mis .* sur ton PATH \(nouveaux terminaux\)") `
          "codex installer: the pinned one runs: its release, no question, the chosen CODEX_HOME, no Voyage key nor install folder of the environment; its PATH line is said"
    $sys = [Environment]::SystemDirectory
    if ($Win) {
        # The stand-in runs under pwsh, which puts its own folder (no tar there) first as it starts; powershell.exe does not.
        $ip = @("$(@($ie | Where-Object { $_ -like 'Path=*' }) | Select-Object -First 1)" -replace '^Path=', '' -split ';' |
                Where-Object { $_ -and $_.TrimEnd('\') -ne $PSHOME.TrimEnd('\') })
        Check ($ip.Count -gt 1 -and $ip[0] -eq $sys -and $ip[1] -eq $gnu) "codex installer: Windows' own tar first on its PATH (a GNU tar ahead of it breaks the installer): $($ip[0..1] -join ';')"
    }
    Check ($env:VOYAGE_API_KEY -eq "pa-canari-voyage" -and $env:CODEX_INSTALL_DIR -eq (Join-Path $T "trap") -and $env:CODEX_HOME -eq (Join-Path $T "other-home") -and
           "$env:Path|$env:PATH" -ceq $pathBefore) "codex installer: the session's own variables are back as they were, PATH too"
    $env:PATH = $pathPut
    $Downloads = @(); $out = Said { Install-Codex }
    Check ($out -match "\[OK\] Codex deja installe \($([regex]::Escape($CODEX_VERSION))\)" -and $Downloads.Count -eq 0) "codex installer: already there, nothing downloaded"
    $env:FAKE_CODEX_VERSION = "0.150.0"; $out = Said { Install-Codex }
    Check ($out -match "\[!\]  Codex 0\.150\.0 .* est plus ancien que ce qu'il faut au kit") "codex installer: an older Codex is said, not replaced"
    New-Home; $out = Said { Install-Codex }
    Check ($out -match "telechargement impossible" -and -not (Find-Codex)) "codex installer: no network, said"
    # npm's codex.cmd (elsewhere, a codex without .exe) is not the kit's Codex: a codex.exe only.
    New-Home; $npm = Join-Path $T "npm"; New-Item -ItemType Directory -Path $npm -Force | Out-Null
    $shim = Join-Path $npm $(if ($Win) { "codex.cmd" } else { "codex" })
    Set-Content -LiteralPath $shim -Value $(if ($Win) { "@exit /b 0" } else { "#!/bin/sh" }) -Encoding ascii
    if (-not $Win) { chmod 755 $shim }
    $p = $env:PATH; $env:PATH = "$npm$([IO.Path]::PathSeparator)$p"
    try { $found = Find-Codex } finally { $env:PATH = $p }
    Check (-not $found) "codex installer: a codex.cmd on the PATH (npm's) does not count, a codex.exe only: $found"
    New-Home; $Serve = Join-Path $T "empty.ps1"; Set-Content -LiteralPath $Serve -Value "exit 0" -Encoding ascii
    $CODEX_INSTALLER_SHA256 = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([IO.File]::ReadAllBytes($Serve))) -replace '-', '').ToLowerInvariant()
    $out = Said { Install-Codex }
    Check ($out -match "Codex ne s'est pas installe") "codex installer: an installer that leaves no codex.exe, said"

    # hooks.json: one that does not parse stops the install at preflight, untouched; otherwise merged.
    New-Home
    $hj = Join-Path $CodexDir "hooks.json"
    Set-Content -LiteralPath $hj -Value '{"hooks": {,}}' -Encoding ascii -NoNewline; Copy-Item $hj (Join-Path $T "ref")
    $r = Invoke-Wire @("check", $CodexDir, $HOOKS)
    Check ($WireCode -eq 1 -and "$r" -match "n'est pas du JSON valide \(ligne 1, colonne" -and "$r" -match "Laisse tel quel" -and (Same $hj (Join-Path $T "ref"))) `
          "codex hooks.json: one that does not parse is left untouched, said in French with its line: $r"
    Set-Content -LiteralPath $hj -Encoding utf8 -Value '{"x": 1, "hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "mine.cmd"}]}]}}'
    Copy-Item $hj (Join-Path $T "ref")
    Set-Content -LiteralPath (Join-Path $CodexDir "config.toml") -Encoding utf8 -NoNewline -Value "model = `"gpt-x`"`n[sandbox_workspace_write]`nnetwork_access = false`n"
    Copy-Item (Join-Path $CodexDir "config.toml") (Join-Path $T "ref-toml")
    Put-Codex; $null = Calls
    $out = Said { Wire-Codex }
    $c = Calls
    $d = Read-Text $hj | ConvertFrom-Json
    $mine = @($d.hooks.Stop[1].hooks | ForEach-Object { "$($_.command)|$($_.commandWindows)|$($_.timeout)" })
    $want = @("session-logger.py", "session-indexer.py", "session-recap.py" | ForEach-Object { "$(Hook-Command $_)|$(Hook-Command $_)|30" })
    Check ($d.x -eq 1 -and $d.hooks.Stop[0].hooks[0].command -eq "mine.cmd" -and ($mine -join "`n") -ceq ($want -join "`n") -and
           $d.hooks.UserPromptSubmit[0].hooks[0].commandWindows -ceq (Hook-Command "correction-detector.py") -and
           $d.hooks.SessionStart[0].hooks[1].commandWindows -ceq (Hook-Command "controle-sessionstart.py") -and (Baks "hooks.json") -eq 1) `
          "codex hooks.json: the user's hooks and keys kept, the kit's after them: & '<python.exe>' '<hook>' --agent codex, in commandWindows and command, 30 s"
    Check ($out -match "hooks du kit ajoutes \(version precedente : ~[\\/]\.codex[\\/]hooks\.json\.bak-") "codex hooks.json: the merge is said, with the previous version"
    $toml = Read-Text (Join-Path $CodexDir "config.toml")
    $got = @{}
    foreach ($m in [regex]::Matches($toml, '\[hooks\.state\.("(?:[^"\\]|\\.)*")\]\ntrusted_hash = "([^"]+)"\n')) { $got[($m.Groups[1].Value | ConvertFrom-Json)] = $m.Groups[2].Value }
    $key = if ($Win) { Join-Path $CodexDir "hooks.json" } else { "$CodexDir\hooks.json" }
    Check ($got.Count -eq 6 -and $got["${key}:user_prompt_submit:0:0"] -ceq (Trust "user_prompt_submit" "correction-detector.py") -and
           $got["${key}:stop:1:2"] -ceq (Trust "stop" "session-recap.py") -and $got["${key}:session_start:0:0"] -ceq (Trust "session_start" "gbrain-update-check.py") -and
           $toml.StartsWith((Read-Text (Join-Path $T "ref-toml")) + "`n# brain-in-a-box: the kit's Codex hooks, approved for you by install.ps1 (see or turn them off with /hooks)`n")) `
          "codex approval: the user's config.toml kept above one table per hook of the kit's, at its place, with the hash of the command Codex runs on Windows"
    Check ($out -match "le kit a approuve ses 6 hooks pour toi \(.*config\.toml, version precedente : .*config\.toml\.bak-.*\), Codex les lance donc sans demander\. Vois-les, ou coupe-les, avec /hooks dans Codex" -and
           @($c | Where-Object { $_ -like "codex features list CODEX_HOME=$CodexDir" }).Count -eq 2 -and (Test-Path (Join-Path $H (Join-Path ".brain-in-a-box" "codex-hooks-poses.json")))) `
          "codex approval: Codex checked config.toml before and after, the install noted, the approval said on screen"
    Copy-Item $hj (Join-Path $T "ref"); Copy-Item (Join-Path $CodexDir "config.toml") (Join-Path $T "ref-toml")
    $out = Said { Wire-Codex }
    Check ((Same $hj (Join-Path $T "ref")) -and (Same (Join-Path $CodexDir "config.toml") (Join-Path $T "ref-toml")) -and (Baks "hooks.json") -eq 1 -and (Baks "config.toml") -eq 1 -and
           $out -match "les hooks du kit y sont deja" -and $out -match "les 6 hooks du kit sont approuves, par le kit pour toi") "codex approval: a re-run writes nothing, and says why"
    $lines = @((Invoke-Wire @("merge", $CodexDir, $HOOKS)) + (Invoke-Wire @("approve", $CodexDir, $HOOKS)) + (Invoke-Wire @("check", (Join-Path $T "nowhere"), $HOOKS)))
    Check (-not ($lines -match '[^\x00-\x7F]')) "codex: wire.py's French lines are ASCII, as install.ps1's own: $lines"

    # What the kit never writes over, and says.
    New-Home; Put-Codex; $ct = Join-Path $CodexDir "config.toml"
    Set-Content -LiteralPath $ct -Encoding utf8 -NoNewline -Value "[hooks]`n"; Copy-Item $ct (Join-Path $T "ref")
    $out = Said { Wire-Codex }
    Check ((Same $ct (Join-Path $T "ref")) -and (Baks "config.toml") -eq 0 -and $out -match "a une table \[hooks\.\.\.\] d'une autre forme, donc le kit n'a pas approuve ses hooks : ouvre Codex, tape /hooks et approuve-les") `
          "codex approval: hooks in another form in config.toml: refused, the file untouched, said"
    New-Home; Put-Codex; Set-Content -LiteralPath $ct -Encoding utf8 -NoNewline -Value "model = `"gpt-x`"`n"; Copy-Item $ct (Join-Path $T "ref")
    $env:FAKE_FEATURES_AFTER = Join-Path $T "after"
    $out = Said { Wire-Codex }
    Check ((Same $ct (Join-Path $T "ref")) -and $out -match "Codex refuse config\.toml avec l'approbation du kit .* remis tel quel") "codex approval: a config.toml Codex refuses once approved is put back byte for byte, and said"
    New-Home; Put-Codex; $env:FAKE_FEATURES_AFTER = Join-Path $T "after"; New-Item -ItemType File -Path $env:FAKE_FEATURES_AFTER | Out-Null
    $out = Said { Wire-Codex }
    Check (-not (Test-Path $ct) -and (Test-Path (Join-Path $CodexDir "hooks.json")) -and $out -match "remis tel quel") "codex approval: no config.toml before, none after a refusal"
    New-Home; Put-Codex; Set-Content -LiteralPath $ct -Value "model = " -Encoding utf8; Copy-Item $ct (Join-Path $T "ref"); $env:FAKE_FEATURES_RC = "1"
    $out = Said { Wire-Codex }
    Check ((Same $ct (Join-Path $T "ref")) -and (Baks "config.toml") -eq 0 -and $out -match "Codex ne sait pas lire .*config\.toml tel quel") "codex approval: a config.toml Codex already cannot read is never written to"
    New-Home
    $out = Said { Wire-Codex }
    Check ((Test-Path (Join-Path $CodexDir "hooks.json")) -and -not (Test-Path $ct) -and $out -match "Codex n'est pas installe : le kit n'a pas pu y approuver ses hooks") "codex approval: without Codex, hooks.json written, nothing approved, and said"
    New-Home; Put-Codex; $null = Invoke-Wire @("merge", $CodexDir, $HOOKS)
    $k = if ($Win) { Join-Path $CodexDir "hooks.json" } else { "$CodexDir\hooks.json" }
    Set-Content -LiteralPath $ct -Encoding utf8 -NoNewline -Value ("[hooks.state." + ($k + ":stop:0:1" | ConvertTo-Json) + "]`ntrusted_hash = `"sha256:" + ("0" * 64) + "`"`nenabled = false`n")
    $out = Said { Wire-Codex }
    Check ($out -match "le kit a approuve ses 5 hooks" -and $out -match "l'approbation que tu as reglee dans /hooks pour session-indexer\.py est gardee telle quelle") "codex approval: a hook the user set in /hooks is left as set, the other five approved"
    # Another Python since the last install (a new version, the old one gone): the kit's hooks run with this
    # install's, as Claude's in settings.json do, and their approval follows.
    New-Home; Put-Codex
    $wire = Join-Path $Kit (Join-Path "engine" (Join-Path "codex" "wire.py"))
    $old = Join-Path $T (Join-Path "Python311" "python.exe")
    $null = & $Py -I $wire --ps1 $old merge $CodexDir $HOOKS; $null = & $Py -I $wire --ps1 $old approve $CodexDir $HOOKS
    $out = Said { Wire-Codex }
    $d = Read-Text $hj | ConvertFrom-Json
    $cmds = @(@($d.hooks.UserPromptSubmit.hooks) + @($d.hooks.Stop.hooks) + @($d.hooks.SessionStart.hooks) | ForEach-Object { "$($_.command)|$($_.commandWindows)" })
    $want = @("correction-detector.py", "session-logger.py", "session-indexer.py", "session-recap.py", "gbrain-update-check.py", "controle-sessionstart.py" |
              ForEach-Object { "$(Hook-Command $_)|$(Hook-Command $_)" })
    $toml = Read-Text $ct
    $got = @{}
    foreach ($m in [regex]::Matches($toml, '\[hooks\.state\.("(?:[^"\\]|\\.)*")\]\ntrusted_hash = "([^"]+)"\n')) { $got[($m.Groups[1].Value | ConvertFrom-Json)] = $m.Groups[2].Value }
    Check (($cmds -join "`n") -ceq ($want -join "`n") -and $got.Count -eq 6 -and $got["${key}:user_prompt_submit:0:0"] -ceq (Trust "user_prompt_submit" "correction-detector.py") -and
           $got["${key}:stop:0:2"] -ceq (Trust "stop" "session-recap.py") -and $got["${key}:session_start:0:1"] -ceq (Trust "session_start" "controle-sessionstart.py") -and
           $out -match "les hooks du kit tournent maintenant avec" -and $out -match "le kit a approuve ses 6 hooks pour toi") `
          "codex approval: another Python since the last install: the kit's hooks run with this one, approved again, and said"

    # The global AGENTS.md: the kit's block once, after the user's text; in AGENTS.override.md when that is the one Codex reads.
    New-Home; $am = Join-Path $CodexDir "AGENTS.md"; Set-Content -LiteralPath $am -Value "My own rules." -Encoding utf8
    $out1 = Said { Codex-Global }; $out2 = Said { Codex-Global }
    $text = Read-Text $am
    Check ($text.StartsWith("My own rules.") -and ([regex]::Matches($text, '<!-- brain-in-a-box -->')).Count -eq 1 -and $text.Contains("Codex does not load ``CLAUDE.md`` files.") -and
           $out1 -match "bloc du kit ajoute \(ton contenu est garde\)" -and $out2 -match "a deja le bloc du kit \(garde tel quel\)") "codex AGENTS.md: the user's text kept, the kit's block once, said"
    New-Home; Set-Content -LiteralPath (Join-Path $CodexDir "AGENTS.override.md") -Value "Override." -Encoding utf8
    $out = Said { Codex-Global }
    Check ((Read-Text (Join-Path $CodexDir "AGENTS.override.md")).Contains("<!-- brain-in-a-box -->") -and -not (Test-Path (Join-Path $CodexDir "AGENTS.md")) -and
           $out -match "Codex lit ce fichier a la place d'AGENTS\.md") "codex AGENTS.md: an AGENTS.override.md with text gets the block, and that is said"

    # gbq's rules: none on Windows. gbq is gbq.cmd there, and cmd.exe reads its arguments again: the "&" of
    # gbq query 'x&calc' would start a second command, outside Codex's sandbox, without asking. Then the
    # vault's AGENTS.md, only when missing.
    New-Home; $out = Said { Codex-Rules }
    Check (-not (Test-Path -LiteralPath (Join-Path $CodexDir "rules")) -and $out -match "Codex demande avant chaque gbq" -and $out -match "cmd\.exe") `
          "codex rules: none on Windows (gbq.cmd goes through cmd.exe, which reads an & in its arguments again): Codex asks first, and that is said"
    $vault = Join-Path $T "vault"; New-Item -ItemType Directory -Path $vault -Force | Out-Null; Remove-Item (Join-Path $vault "AGENTS.md") -ErrorAction SilentlyContinue
    $null = Said { Codex-Vault $vault }
    $placed = Same (Join-Path $vault "AGENTS.md") (Join-Path $Kit (Join-Path "engine" (Join-Path "codex" "AGENTS.vault.md")))
    Set-Content -LiteralPath (Join-Path $vault "AGENTS.md") -Value "mine" -Encoding utf8; $out = Said { Codex-Vault $vault }
    Check ($placed -and (Read-Text (Join-Path $vault "AGENTS.md")).Trim() -eq "mine" -and $out -match "deja la \(garde tel quel\)") "codex vault: AGENTS.md placed when missing, the user's kept"

    # The question: a new install at a console, nothing chosen, where Codex can be chosen.
    $open = Join-Path $T "kit-open"
    New-Item -ItemType Directory -Path (Join-Path $open (Join-Path "engine" "hooks")) -Force | Out-Null
    $gate = Read-Text (Join-Path $Kit (Join-Path "engine" (Join-Path "hooks" "_agents.py")))
    # Whatever the gate says (closed today, open on some systems once a change opens them).
    Check (([regex]::Matches($gate, '(?m)^CODEX_OUVERT = frozenset\(.*\)\r?$')).Count -eq 1) "codex question: the gate's line, opened in the copy"
    [IO.File]::WriteAllText((Join-Path $open (Join-Path "engine" (Join-Path "hooks" "_agents.py"))), ($gate -replace '(?m)^CODEX_OUVERT = frozenset\(.*\)(?=\r?$)', 'CODEX_OUVERT = frozenset({"darwin", "linux", "windows"})'))
    function Ask([string[]]$Given, [string]$RepoDir = $open) {
        New-Home $RepoDir; $script:Interactive = $true; $script:Answers = $Given
        $null = Said { Ask-Agents }
        return "$Agents/$Prefere/$($Asked.Count)"
    }
    $bad = @()
    if ((Ask @("n")) -ne "claude//1") { $bad += "no" }
    if ((Ask @("o", "n")) -ne "codex//2") { $bad += "codex" }
    if ((Ask @("oui", "y", "o")) -ne "claude,codex/codex/3") { $bad += "both-codex" }
    if ((Ask @("y", "yes", "")) -ne "claude,codex/claude/3") { $bad += "both-claude" }
    if ((Ask @("")) -ne "claude//1") { $bad += "enter" }
    Check ($bad.Count -eq 0) "codex question: a new install where Codex can be chosen asks: Claude alone (Enter), Codex, both and who writes the evening summary: $bad"
    $bad = @()
    if ((Ask @("o") $Kit) -ne "//0") { $bad += "closed" }   # the gate as it is in the repository
    New-Home $open; $Answers = @("o"); $Interactive = $false; $null = Said { Ask-Agents }; if ($Asked.Count) { $bad += "no-console" }
    foreach ($there in $MANIFEST, (Join-Path $HOOKS "correction-detector.py"), $AgentsFile) {
        New-Home $open; $Interactive = $true; $Answers = @("o"); Set-Content -LiteralPath $there -Value "{}"
        $null = Said { Ask-Agents }; if ($Asked.Count) { $bad += (Split-Path -Leaf $there) }
    }
    New-Home $open; $Interactive = $true; $Answers = @("o"); $env:BIAB_AGENTS = "claude"; $null = Said { Ask-Agents }; if ($Asked.Count) { $bad += "BIAB_AGENTS" }
    New-Home $open; $Interactive = $true; $Answers = @("o"); $Agents = "claude"; $null = Said { Ask-Agents }; if ($Asked.Count) { $bad += "-Agents" }
    Check ($bad.Count -eq 0) "codex question: never asked where Codex is closed, without a console, over an install from before, an agents.json or a choice given: $bad"
} finally {
    foreach ($n in $saved.Keys) { if ($null -eq $saved[$n]) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } else { Set-Item "Env:$n" $saved[$n] } }
    Remove-Item -LiteralPath $T -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output ""
Write-Output "$Failures failure(s)"
if ($Failures) { exit 1 }
