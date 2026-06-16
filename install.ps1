<#
.SYNOPSIS
  brain-in-a-box - installeur Windows natif (second cerveau perso).
  Equivalent de install.sh, sans gbrain : la recherche tourne en local via
  brain_search (BM25 pur Python) -> marche sur Windows sans pgvector (#1549).
  Idempotent et non-destructif (ne remplace jamais un vault/CLAUDE.md existant).

.NOTES
  Scheduler = Task Scheduler (vs launchd). Recherche = engine/search/brain_search.py.
  Le shim gbq.cmd mappe `gbq query` -> brain_search, donc le CLAUDE.md reste inchange.
  Pre-requis : Windows 10/11, git, python 3, Claude Code.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
#>

[CmdletBinding()]
param([string]$BrainDir = "$env:USERPROFILE\Documents\Brain")

$ErrorActionPreference = "Stop"
$REPO = Split-Path -Parent $MyInvocation.MyCommand.Path
$H = $env:USERPROFILE
$HOOKS = "$H\.claude\hooks\brain"
$BIN = "$H\.local\bin"
$BSDIR = "$H\.brain-search"

function Say($t)  { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}

# 1. Preflight
Say "Preflight"
if (-not (Have git))    { if (Have winget) { winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements | Out-Null; Refresh-Path } }
if (-not (Have git))    { Warn "git introuvable - installe-le puis relance"; exit 1 } else { Ok "git" }
if (-not (Have python)) { if (Have winget) { winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null; Refresh-Path } }
$Py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { Warn "python introuvable - rouvre un terminal apres install et relance"; exit 1 } else { Ok "python ($Py)" }
if (Have claude) { Ok "claude (Claude Code)" } else { Warn "Claude Code absent - l'app desktop suffit ; la reflection nocturne (claude -p) le requiert" }

# 2. Vault skeleton (jamais ecrase)
Say "Brain vault ($BrainDir)"
if (Test-Path $BrainDir) {
    Warn "vault deja present -> skeleton NON copie (on garde ton contenu)"
} else {
    New-Item -ItemType Directory -Path $BrainDir -Force | Out-Null
    Copy-Item "$REPO\vault-skeleton\*" $BrainDir -Recurse -Force
    Ok "skeleton place"
}

# 3. Hooks (cross-platform, simple copie)
Say "Brain hooks ($HOOKS)"
New-Item -ItemType Directory -Path $HOOKS -Force | Out-Null
Copy-Item "$REPO\engine\hooks\*.py" $HOOKS -Force
Ok "hooks copies"

# 4. brain_search (recherche locale) + index + shim gbq
Say "brain_search (recherche locale, sans gbrain)"
New-Item -ItemType Directory -Path $BSDIR -Force | Out-Null
New-Item -ItemType Directory -Path $BIN -Force | Out-Null
Copy-Item "$REPO\engine\search\brain_search.py" "$BSDIR\brain_search.py" -Force
$env:BRAIN_DIR = $BrainDir
& $Py "$BSDIR\brain_search.py" index --brain $BrainDir
# shim gbq.cmd : `gbq query "..."` -> brain_search
$gbq = "@echo off`r`nset BRAIN_DIR=$BrainDir`r`n`"$Py`" `"%USERPROFILE%\.brain-search\brain_search.py`" %*`r`n"
Set-Content -Path "$BIN\gbq.cmd" -Value $gbq -Encoding ascii
$userPath = [Environment]::GetEnvironmentVariable("PATH","User")
if ($userPath -notlike "*$BIN*") { [Environment]::SetEnvironmentVariable("PATH","$userPath;$BIN","User") }
[Environment]::SetEnvironmentVariable("BRAIN_DIR",$BrainDir,"User")
Ok "gbq installe ($BIN\gbq.cmd) + index construit"

# 5. settings.json (merge non-destructif des hooks)
Say "Hooks Claude (settings.json)"
$settings = "$H\.claude\settings.json"
$merge = @'
import json, os, sys
settings, pyexe, hookdir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(settings), exist_ok=True)
d = {}
if os.path.exists(settings):
    try: d = json.load(open(settings, encoding="utf-8"))
    except Exception: d = {}
hooks = d.setdefault("hooks", {})
def cmd(name): return {"type": "command", "command": '"%s" "%s"' % (pyexe, os.path.join(hookdir, name))}
def ensure(evt, names):
    arr = hooks.setdefault(evt, [])
    grp = next((g for g in arr if g.get("matcher", "") == ""), None)
    if grp is None:
        grp = {"matcher": "", "hooks": []}; arr.append(grp)
    have = {h.get("command") for h in grp.setdefault("hooks", [])}
    for n in names:
        c = cmd(n)
        if c["command"] not in have: grp["hooks"].append(c)
ensure("UserPromptSubmit", ["correction-detector.py"])
ensure("Stop", ["session-logger.py", "session-indexer.py", "session-recap.py"])
json.dump(d, open(settings, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ok settings.json")
'@
$merge | & $Py - $settings $Py $HOOKS
Ok "hooks UserPromptSubmit/Stop cables"

# 6. Global CLAUDE.md (append, jamais ecrase)
Say "CLAUDE.md global"
$GC = "$H\.claude\CLAUDE.md"
$mark = "<!-- brain-in-a-box -->"
$cur = if (Test-Path $GC) { Get-Content $GC -Raw } else { "" }
if ($cur -match [regex]::Escape($mark)) {
    Ok "deja present (skip)"
} else {
    $tpl = Get-Content "$REPO\engine\CLAUDE.global.template.md" -Raw -Encoding utf8
    Add-Content -Path $GC -Value "`r`n$mark`r`n$tpl" -Encoding utf8
    Ok "ajoute (ton contenu existant preserve)"
}

# 7. Task Scheduler (reindex nuit + reflection 12h/23h)
Say "Task Scheduler (reindex 04:00 + reflection 12:00/23:00)"
function Register-BrainTask($name, $argument, $triggers) {
    $action = New-ScheduledTaskAction -Execute $Py -Argument $argument
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Force -User $env:USERNAME | Out-Null
    Ok "tache: $name"
}
try {
    Register-BrainTask "BrainSearchReindex" "`"$BSDIR\brain_search.py`" index" (New-ScheduledTaskTrigger -Daily -At "04:00")
    $t12 = New-ScheduledTaskTrigger -Daily -At "12:00"
    $t23 = New-ScheduledTaskTrigger -Daily -At "23:00"
    Register-BrainTask "BrainReflection" "`"$HOOKS\daily-reflection.py`"" @($t12, $t23)
} catch {
    Warn "Task Scheduler : $($_.Exception.Message) - recree les taches a la main si besoin"
}

# 8. git init vault
Say "git init vault"
if (-not (Test-Path "$BrainDir\.git")) {
    Push-Location $BrainDir
    ".obsidian/`n.DS_Store`n" | Set-Content .gitignore -Encoding ascii
    git init -q
    git add -A
    git -c user.email="brain@local" -c user.name="brain" commit -q -m "brain init"
    Pop-Location
    Ok "repo git cree"
} else { Ok "deja un repo git" }

# 9. Verifier la recherche
Say "Verification"
& $Py "$BSDIR\brain_search.py" health

Write-Host ""
Write-Host "brain-in-a-box installe (Windows natif)." -ForegroundColor Green
Write-Host ""
Write-Host "Ouvre un NOUVEAU terminal puis teste :" -ForegroundColor Cyan
Write-Host "  gbq query `"test`"          # la memoire repond (recherche locale)"
Write-Host "  cd `"$BrainDir`" ; claude   # session dans le brain"
Write-Host ""
Write-Host "Reindex auto chaque nuit (Task Scheduler). Reflection 12h/23h si claude est connecte." -ForegroundColor Cyan
