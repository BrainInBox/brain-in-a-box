# A Windows whose winget cannot open its source, as a GitHub VM's on 2026-10-22 (0x8A15000F, "Data
# required by the source is missing"); on a PC: a source never updated, a company network, an old App
# Installer. A stand-in winget.exe, first on the Machine PATH (which install.ps1 reads again) and on this
# process's, answers every command as that one did, and notes each in $WingetCalls. Nothing finds a
# Python either, but the Store stub (poste-neuf.ps1, which also takes the py launcher away): the runner's
# Pythons leave the user's PATH too. Then the proof, in a new process whose PATH is read from the registry.
# Dot-source it after poste-neuf.ps1 and sans-git.ps1, in the step that runs the installer:
#   . .github/ci/winget-casse.ps1
$ErrorActionPreference = 'Stop'
$WingetCasse = Join-Path $env:RUNNER_TEMP 'winget-casse'
$WingetCalls = Join-Path $WingetCasse 'appels.txt'
New-Item -ItemType Directory $WingetCasse -Force | Out-Null
Set-Content "$WingetCasse\winget.cs" -Encoding ascii -Value @"
using System; using System.IO;
class W { static int Main(string[] a) {
    File.AppendAllText(@"$WingetCalls", string.Join(" ", a) + "\n");
    if (a.Length == 1 && a[0] == "--version") { Console.WriteLine("v1.11.510"); return 0; }
    Console.WriteLine("Failed when opening source(s); try the 'source reset' command if the problem persists.");
    Console.WriteLine("An unexpected error occurred while executing the command: ");
    Console.WriteLine("0x8a15000f : Data required by the source is missing");
    return -1978335217;
} }
"@
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo "/out:$WingetCasse\winget.exe" "$WingetCasse\winget.cs"
if ($LASTEXITCODE) { throw 'the stand-in winget.exe did not compile' }
[Environment]::SetEnvironmentVariable('Path', "$WingetCasse;" + [Environment]::GetEnvironmentVariable('Path', 'Machine'), 'Machine')
$env:Path = "$WingetCasse;$env:Path"

$tc = '*\hostedtoolcache\windows\Python\*'
$u = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($u) { [Environment]::SetEnvironmentVariable('Path', ((($u -split ';') | Where-Object { $_ -notlike $tc }) -join ';'), 'User') }

# The proof: a new process with the PATH Windows gives a new program finds the stand-in winget first, and
# no Python that runs code (the Store's aliases exit 9009).
$enfant = @'
$w = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$py = @()
foreach ($n in 'python', 'python3', 'py') {
    foreach ($c in @(Get-Command $n -All -CommandType Application -ErrorAction SilentlyContinue)) {
        $null = & $c.Source -c "print(1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $py += $c.Source }
    }
}
"$(if ($w) { $w.Source })|$($py -join ',')"
'@
$avant = $env:Path
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
try {
    $lu = "$(& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -EncodedCommand ([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($enfant))))".Trim()
} finally { $env:Path = $avant }
$w, $py = $lu -split '\|', 2
"a new process: winget $w ; a Python that runs: $(if ($py) { $py } else { 'none' })"
if ($w -ne "$WingetCasse\winget.exe") { throw "a new process takes $w for winget, not the stand-in" }
if ($py) { throw "a new process still finds a Python that runs: $py" }
Remove-Item -LiteralPath $WingetCalls -ErrorAction SilentlyContinue
