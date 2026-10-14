# A Windows PC as users have it, on a GitHub runner: no Python but the Microsoft Store stub
# (what broke installs in the field), so the installer has to get Python from winget.
# Dot-source it in the step that runs the installer:  . .github/ci/poste-neuf.ps1
# The runner's own Python sits on the Machine PATH, which the installer re-reads: it is dropped
# from it and from this process's PATH. The next steps keep the runner's Python (their PATH
# comes from the runner, not from the Machine PATH), which is what runs the test harnesses.
# Used by test.yml (windows) and terrain.yml (windows, neuf).
$tc = '*\hostedtoolcache\windows\Python\*'
$m = ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';') | Where-Object { $_ -notlike $tc }
[Environment]::SetEnvironmentVariable('Path', ($m -join ';'), 'Machine')
$stub = Join-Path $env:RUNNER_TEMP 'stub'
New-Item -ItemType Directory $stub -Force | Out-Null
Set-Content "$stub\python.cmd" "@echo Python was not found; run without arguments to install from the Microsoft Store.`r`n@exit /b 9009" -Encoding ascii
# The py launcher would find the toolcache Pythons through the registry: a machine without Python has none.
Set-Content "$stub\py.cmd" "@echo No installed Python found!`r`n@exit /b 103" -Encoding ascii
$env:Path = "$stub;" + ((($env:Path -split ';') | Where-Object { $_ -notlike $tc }) -join ';')
