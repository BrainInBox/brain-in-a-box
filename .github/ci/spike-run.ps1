# Spike only (never merged): install.ps1's Official-Install for real, as whoever runs this script.
param([Parameter(Mandatory = $true)][string]$Out, [string]$Kit = "")
$ErrorActionPreference = "Stop"
if (-not $Kit) { $Kit = Split-Path -Parent $MyInvocation.MyCommand.Path }
$r = [ordered]@{}
try {
    $errs = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Kit "install.ps1"), [ref]$null, [ref]$errs)
    foreach ($s in $ast.EndBlock.Statements) {
        if ($s -is [Management.Automation.Language.FunctionDefinitionAst] -or
            ($s -is [Management.Automation.Language.AssignmentStatementAst] -and ($s.Left.Extent.Text -clike '$WINGET_*' -or $s.Left.Extent.Text -clike '$OFFICIAL_*'))) {
            . ([scriptblock]::Create($s.Extent.Text))
        }
    }
    $H = $env:USERPROFILE
    $LOG = Join-Path $H ".brain-in-a-box\install.log"
    New-Item -ItemType Directory -Path (Split-Path -Parent $LOG) -Force | Out-Null
    $LogOpen = $true; $WingetNotes = @()
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $r.user = $id.Name
    $r.admin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $r.machine_arch = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE', 'Machine')
    $r.process_arch = $env:PROCESSOR_ARCHITECTURE
    $r.arch = Get-WinArch
    $r.ps = "$($PSVersionTable.PSVersion)"
    $r.localappdata = $env:LOCALAPPDATA
    foreach ($k in "Git", "Python") {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r["$k-ok"] = Official-Install $k
        $r["$k-seconds"] = [int]$sw.Elapsed.TotalSeconds
    }
    function Machine([string]$exe) {
        $b = [IO.File]::ReadAllBytes($exe); $pe = [BitConverter]::ToInt32($b, 0x3C)
        return ('0x{0:X4}' -f [BitConverter]::ToUInt16($b, $pe + 4))
    }
    foreach ($g in "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe", "$env:ProgramFiles\Git\cmd\git.exe") {
        if (Test-Path -LiteralPath $g) { $r["git:$g"] = "$(& $g --version) machine=$(Machine $g) created=$((Get-Item (Split-Path $g)).CreationTimeUtc.ToString('o'))" }
    }
    foreach ($p in @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue)) {
        $r["python:$($p.FullName)"] = "$(& $p.FullName -c 'import platform, sys; print(platform.machine(), sys.version.split()[0])') machine=$(Machine $p.FullName)"
    }
    $r.find_git = Find-Git
    $r.find_python = Find-Python
    $r.user_path = [Environment]::GetEnvironmentVariable('Path', 'User')
    $r.leftovers = @(Get-ChildItem -LiteralPath (Join-Path $H ".brain-in-a-box") -Directory -Filter "installeur-*" | ForEach-Object { $_.Name })
    $r.log = [IO.File]::ReadAllText($LOG)
} catch {
    $r.error = "$($_.Exception.Message) at $($_.InvocationInfo.PositionMessage)"
}
[IO.File]::WriteAllText($Out, ($r | ConvertTo-Json -Depth 4))
