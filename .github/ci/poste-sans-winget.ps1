# A Windows PC without winget, as Windows 10 LTSC, a Windows Server, a company that took App Installer away,
# or its alias turned off (Settings > Apps > App execution aliases) leave it: install.ps1 then brings Python
# and Git through their official installers, and no Obsidian (winget only). On a GitHub runner, winget is the
# alias App Installer puts in the user's WindowsApps folder: every winget.exe found in a folder of the PATH
# (the machine's, the user's, this process's) under a WindowsApps folder goes, as turning the alias off does.
# Then the proof that nothing finds winget any more, here and in a new process whose PATH is read again from
# the registry: Get-Command, where.exe, each folder of the PATH. Written to -Preuve (JSON), read again by
# terrain.py (preuve_sans_winget); a winget still found stops here, the proof saying where.
#
#   powershell -File .github/ci/poste-sans-winget.ps1 -Preuve <proof.json>   terrain.py (app-windows, app-sans-winget),
#                                                                          after sans-git.ps1, which wants winget there
param([Parameter(Mandatory = $true)][string]$Preuve)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# A native command's stderr never stops the script (Windows PowerShell 5.1 makes it an error under Stop).
function Invoke-Natif([scriptblock]$c) {
    $ErrorActionPreference = 'Continue'
    & $c 2>$null
}

function Get-Dossiers {
    $p = @([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'), $env:Path) -join ';'
    return @($p -split ';' | ForEach-Object { "$_".Trim().Trim('"').TrimEnd('\') } | Where-Object { $_ -match '^[A-Za-z]:\\' } | Select-Object -Unique)
}

# A WindowsApps alias is a link Test-Path may not follow: listed, it is there.
function Get-Winget([string]$d) {
    return @(Get-ChildItem -LiteralPath $d -Filter 'winget.exe' -Force -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
}

$retires = @()
$restes = @()
foreach ($d in Get-Dossiers) {
    foreach ($w in Get-Winget $d) {
        if ($d -like '*\WindowsApps') {
            Remove-Item -LiteralPath $w -Force
            $retires += $w
        } else {
            $restes += $w
        }
    }
}

$chemins = @()
foreach ($d in Get-Dossiers) { $chemins += Get-Winget $d }
$cmd = @(Get-Command winget -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
$where = @(Invoke-Natif { & "$env:SystemRoot\System32\where.exe" winget } | Where-Object { $_ })

# A new process with the PATH Windows gives a new program.
$enfant = @'
$c = @(Get-Command winget -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
& "$env:SystemRoot\System32\where.exe" winget 2>$null | Out-Null
"$LASTEXITCODE|$($c -join ',')"
'@
$avant = $env:Path
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
try {
    $code64 = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($enfant))
    $lu = ((Invoke-Natif { & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -EncodedCommand $code64 }) -join '').Trim()
} finally { $env:Path = $avant }
if ($lu -notmatch '^-?\d+\|') { throw "a new process said nothing readable: $lu" }
$code, $trouves = $lu -split '\|', 2
$neuf = [ordered]@{ get_command = @("$trouves" -split ',' | Where-Object { $_ }); where_code = [int]$code }

$json = [ordered]@{
    retires = $retires
    sondes = [ordered]@{ get_command = $cmd; where = $where; chemins = $chemins }
    nouveau_processus = $neuf
} | ConvertTo-Json -Depth 4
New-Item -ItemType Directory -Path (Split-Path -Parent $Preuve) -Force | Out-Null
[IO.File]::WriteAllText($Preuve, $json, (New-Object Text.UTF8Encoding $false))

"winget retire : $(if ($retires) { $retires -join ', ' } else { 'rien a retirer' })"
"a new process: where winget code $code ; Get-Command: $(if ($trouves) { $trouves } else { 'none' })"
if ($restes) { throw "a winget.exe outside a WindowsApps folder: $($restes -join ', ')" }
if ($chemins -or $cmd -or $where -or $neuf.get_command -or $neuf.where_code -eq 0) { throw 'winget is still found' }
