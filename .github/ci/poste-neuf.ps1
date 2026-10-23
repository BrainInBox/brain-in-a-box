# A Windows PC as users have it, on a GitHub runner: no Python but the Microsoft Store stub
# (what broke installs in the field), so the installer has to get Python from winget.
# Dot-source it in the step that runs the installer:  . .github/ci/poste-neuf.ps1
# The runner's own Python sits on the Machine PATH, which the installer re-reads: it is dropped
# from it and from this process's PATH. The next steps keep the runner's Python (their PATH
# comes from the runner, not from the Machine PATH), which is what runs the test harnesses.
# Used by test.yml (windows, app-windows-install) and terrain.yml (windows, neuf; app-windows).
$tc = '*\hostedtoolcache\windows\Python\*'
$m = ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') | Where-Object { $_ -notlike $tc }
[Environment]::SetEnvironmentVariable('Path', ($m -join ';'), 'Machine')
$stub = Join-Path $env:RUNNER_TEMP 'stub'
New-Item -ItemType Directory $stub -Force | Out-Null
# The Store's python.exe and python3.exe: real programs on the PATH that run nothing, say so and
# exit 9009. A .cmd would not do: the app looks for python.exe, as Windows ships it.
if (-not (Test-Path "$stub\python.exe")) {
    Set-Content "$stub\stub.cs" 'class P { static int Main() { System.Console.Error.WriteLine("Python was not found; run without arguments to install from the Microsoft Store, or disable this shortcut from Settings > Manage App Execution Aliases."); return 9009; } }' -Encoding ascii
    & "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo "/out:$stub\python.exe" "$stub\stub.cs"
    if ($LASTEXITCODE) { throw 'the python.exe stub did not compile' }
    Copy-Item "$stub\python.exe" "$stub\python3.exe"
}
# The py launcher would find the toolcache Pythons through the registry: a machine without Python has none.
Set-Content "$stub\py.cmd" "@echo No installed Python found!`r`n@exit /b 103" -Encoding ascii
# The stub comes first only until install.ps1 reads the PATH again after winget (the Machine PATH first): the
# launcher in Windows' folder then answers, with a toolcache Python (a GitHub VM, 2026-10-22, when winget
# failed). Renamed once; the job's later steps find it gone.
foreach ($n in 'py.exe', 'pyw.exe') {
    if (Test-Path -LiteralPath "$env:WINDIR\$n" -PathType Leaf) { Rename-Item -LiteralPath "$env:WINDIR\$n" -NewName "$n.biab-off" }
}
$env:Path = "$stub;" + ((($env:Path -split ';') | Where-Object { $_ -notlike $tc }) -join ';')
