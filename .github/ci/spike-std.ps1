# Spike only (never merged): the pinned official installers, as whoever runs this script, each with its own
# log and a time limit, to see why Python's waited forever as a standard user (run 39763375721).
param([Parameter(Mandatory = $true)][string]$Out, [string]$Kit = "", [string]$Only = "", [int]$Limit = 360)
$ErrorActionPreference = "Stop"
if (-not $Kit) { $Kit = Split-Path -Parent $MyInvocation.MyCommand.Path }
$r = [ordered]@{}
try {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $prof = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$sid").ProfileImagePath
    $r.env_before = "USERPROFILE=$env:USERPROFILE LOCALAPPDATA=$env:LOCALAPPDATA TEMP=$env:TEMP SESSION=$((Get-Process -Id $PID).SessionId)"
    $env:USERPROFILE = $prof
    $env:LOCALAPPDATA = Join-Path $prof 'AppData\Local'
    $env:APPDATA = Join-Path $prof 'AppData\Roaming'
    New-Item -ItemType Directory -Path (Join-Path $env:LOCALAPPDATA 'Temp'), $env:APPDATA -Force | Out-Null
    $env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp'; $env:TMP = $env:TEMP
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $r.user = $id.Name
    $r.admin = ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $r.ps = "$($PSVersionTable.PSVersion)"
    $errs = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $Kit "install.ps1"), [ref]$null, [ref]$errs)
    foreach ($s in $ast.EndBlock.Statements) {
        if ($s -is [Management.Automation.Language.AssignmentStatementAst] -and $s.Left.Extent.Text -clike '$OFFICIAL_*') { . ([scriptblock]::Create($s.Extent.Text)) }
    }
    $logs = Join-Path $Kit "logs"
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $names = if ($Only) { @($Only) } else { @("Python", "Git") }
    foreach ($k in $names) {
        $o = $OFFICIAL_INSTALLERS[$k]
        $url, $sha = $o.x64
        $f = Join-Path $Kit ($url -replace '^.*/', '')
        $ProgressPreference = "SilentlyContinue"
        if (-not (Test-Path -LiteralPath $f)) { Invoke-WebRequest -Uri $url -OutFile $f -UseBasicParsing }
        $hex = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hex -ne $sha) { $r["$k"] = "sha $hex"; continue }
        $a = @($o.Args)
        $a += if ($k -eq "Python") { @("/log", "`"$logs\py.log`"") } else { @("/LOG=`"$logs\git.log`"") }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $p = Start-Process -FilePath $f -ArgumentList $a -PassThru
        $done = $p.WaitForExit($Limit * 1000)
        $r["$k-seconds"] = [int]$sw.Elapsed.TotalSeconds
        if ($done) { $r["$k-code"] = $p.ExitCode } else {
            $r["$k-code"] = "timeout"
            $r["$k-procs"] = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python-3*' -or $_.Name -like 'Git-2*' -or $_.Name -like '*.tmp' -or $_.Name -eq 'consent.exe' } |
                               ForEach-Object { "$($_.ProcessId) $($_.ParentProcessId) $($_.Name) $($_.CommandLine)" })
            Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python-3*' -or $_.Name -like 'Git-2*' -or $_.Name -like 'is-*.tmp' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    foreach ($g in "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe", "$env:ProgramFiles\Git\cmd\git.exe") {
        if (Test-Path -LiteralPath $g) { $r["git:$g"] = "$(& $g --version)" }
    }
    foreach ($p in @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue)) {
        $r["python:$($p.FullName)"] = "$(& $p.FullName -c 'import sys; print(sys.version.split()[0])')"
    }
    $r.user_path = [Environment]::GetEnvironmentVariable('Path', 'User')
} catch {
    $r.error = "$($_.Exception.Message) at $($_.InvocationInfo.PositionMessage)"
}
[IO.File]::WriteAllText($Out, ($r | ConvertTo-Json -Depth 4))
