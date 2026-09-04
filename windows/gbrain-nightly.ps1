# brain-in-a-box nightly maintenance (Windows port of gbrain-nightly.sh).
# Run by Task Scheduler at 04:00.
# Steps: self-update gbrain -> commit personal vault -> sync personal
#        -> [company vault pull + sync if present] -> dream cycle.
#
# No stale-lock sweep here: the macOS PGLite "process won't exit" bug does NOT
# reproduce on Windows (cli-force-exit.ts exits cleanly, verified with
# sequential + concurrent reads). So no kill/lock dance is needed.

$ErrorActionPreference = "Continue"

$Bun     = @("$env:USERPROFILE\.bun\bin\bun.exe", "$env:APPDATA\npm\node_modules\bun\bin\bun.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$Cli     = "$env:USERPROFILE\DEV\gbrain\src\cli.ts"
$Vault   = "$env:USERPROFILE\Documents\Brain"
$VaultCo = "$env:USERPROFILE\Documents\BrainCo"
$GRepo   = "$env:USERPROFILE\DEV\gbrain"
$Log     = "$env:USERPROFILE\.gbrain\nightly.log"
# Mail connector (collecteur metadata + enrichissement LLM -> notes mails/).
$EmailDir    = "$env:USERPROFILE\brain-in-a-box\windows\email-collector"
$EmailCli    = "$EmailDir\email-collector.mjs"
$EmailEnrich = "$EmailDir\email-enrich.py"
$PyExe       = (Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1).Source
if (-not $PyExe) { $PyExe = "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe" }

# All logging goes through one UTF-8 sink so the log stays readable. PowerShell
# 5.1's `*>>` redirect writes UTF-16 (mixes encodings), so we pipe to Add-Content.
function Log($msg)  { Add-Content -Path $Log -Value $msg -Encoding utf8 }
function Run        { & $args[0] @($args[1..($args.Count-1)]) 2>&1 | Add-Content -Path $Log -Encoding utf8 }
# bun runs inline; output piped to the UTF-8 log sink. The 0xC000013A (CTRL_C)
# failures in autonomous/locked-session runs are avoided by LAUNCHING this task
# via a wscript launcher (.vbs, no console) + LogonType Interactive. S4U is NOT
# used (Access Denied on Windows Home). No Start-Process gymnastics needed.
function Gbrain     { (& $Bun run $Cli @args 2>&1) | Add-Content -Path $Log -Encoding utf8 }

Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') nightly start ====="

# 0. Self-update gbrain (resilient: a failure must never block the cycle).
if (Test-Path "$GRepo\.git") {
    Push-Location $GRepo
    $before = (git rev-parse --short HEAD 2>$null)
    Run git pull --ff-only
    $after = (git rev-parse --short HEAD 2>$null)
    if ($before -ne $after) {
        Log "[update] gbrain $before -> $after"
        Run $Bun install
        Gbrain apply-migrations --yes
    }
    Pop-Location
}

# 1. Commit the personal vault first (sync is git-diff based -> without a commit, edits are invisible).
if (Test-Path "$Vault\.git") {
    Push-Location $Vault
    if (git status --porcelain) {
        Run git add -A
        Run git -c user.email="brain@local" -c user.name="brain" commit -q -m "nightly $(Get-Date -Format 'yyyy-MM-dd')"
        Log "[git] personal vault committed"
    }
    Pop-Location
}
# IMPORTANT: pin BOTH --source AND --repo. Syncing with a bare --repo (no
# --source) makes gbrain write that path onto the wrong source row -> it
# repointed 'company' at the personal vault (corruption seen 2026-06-22).
# Pinning each source to its repo is self-healing: a wrong path is re-corrected
# every night.
Gbrain sync --source default --repo $Vault --no-pull

# 2. COMPANY vault (team mode): pull + collecte/enrichissement mail + sync.
if (Test-Path "$VaultCo\.git") {
    Run git -C $VaultCo pull --ff-only

    # 2a. Mail : collecte (metadata, lecture seule) puis enrichissement LLM
    #     (reponses clients -> 1 note par mail dans BrainCo/mails/, affiliees au client).
    if (Test-Path $EmailCli) { Run $Bun $EmailCli run }
    if ((Test-Path $EmailEnrich) -and $PyExe) { Run $PyExe $EmailEnrich }

    # 2b. Commit LOCAL des notes mail (le push reste manuel/volontaire).
    Push-Location $VaultCo
    if (git status --porcelain) {
        Run git add -A
        Run git -c user.email="brain@local" -c user.name="brain" commit -q -m "nightly mails $(Get-Date -Format 'yyyy-MM-dd')"
        Log "[git] company mail notes committed (local)"
    }
    Pop-Location

    Gbrain sync --source company --repo $VaultCo --no-pull
    Log "[sync] company source"
}

# 3. Dream cycle (dedup, facts, consolidation, embed, purge).
Gbrain dream

Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') nightly done ====="
