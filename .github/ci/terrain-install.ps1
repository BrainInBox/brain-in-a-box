# The tested version of brain-in-a-box, installed the way a Windows user does (terrain.yml).
# The scheduled night runs the README's line itself: master, downloaded from GitHub.
# Started by hand on another ref: the same one-liner, fed with that ref's install.ps1 and
# an archive of it (BIAB_ARCHIVE), as test.yml does.
# With TERRAIN_SANS_GIT (the fresh install), the runner's Git goes first, once the archive is made
# (sans-git.ps1, its proof in that file): the installer, started from this process, has to bring Git.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_EVENT_NAME -ne 'schedule') {
    $zip = Join-Path $env:RUNNER_TEMP 'terrain-src.zip'
    git archive --format=zip --prefix=brain-in-a-box-terrain/ -o $zip HEAD
    if ($LASTEXITCODE) { throw "git archive exited $LASTEXITCODE" }
    $env:BIAB_ARCHIVE = $zip
}
if ($env:TERRAIN_SANS_GIT) {
    & (Join-Path $PSScriptRoot 'sans-git.ps1') -Preuve $env:TERRAIN_SANS_GIT
}
if ($env:GITHUB_EVENT_NAME -eq 'schedule') {
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BrainInBox/brain-in-a-box/master/install.ps1 | iex"
} else {
    $script = Join-Path (Get-Location) 'install.ps1'
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Raw '$script' | Invoke-Expression"
}
exit $LASTEXITCODE
