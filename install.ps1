<#
.SYNOPSIS
  brain-in-a-box - installeur Windows natif (second cerveau perso).
  Equivalent de install.sh, sans gbrain : la recherche tourne en local via
  brain_search (BM25 pur Python) -> marche sur Windows sans pgvector (#1549).
  Idempotent et non-destructif (ne remplace jamais un vault/CLAUDE.md existant).
  Installe ce qui manque : Python, Git, Claude Code, Obsidian (winget, sans admin quand c'est possible).

.NOTES
  Scheduler = Task Scheduler (vs launchd). Recherche = engine/search/brain_search.py.
  Le shim gbq.cmd mappe `gbq query` -> brain_search, donc le CLAUDE.md reste inchange.
  Pre-requis : Windows 10 (1809+) ou 11. Le reste s'installe tout seul.
  Lance par `irm | iex`, le script telecharge le repo dans ~\.brain-in-a-box\src
  (BIAB_ARCHIVE = autre archive .zip, URL ou chemin local).

.EXAMPLE
  # Rien a installer avant : Win+R (ou PowerShell, ou l'invite de commandes), colle, Entree
  powershell -NoExit -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/BrainInBox/brain-in-a-box/master/install.ps1 | iex"

.EXAMPLE
  # Depuis un clone du repo (ou le zip extrait)
  powershell -ExecutionPolicy Bypass -File install.ps1
#>

[CmdletBinding()]
param([string]$BrainDir = $(if ($env:BRAIN_DIR) { $env:BRAIN_DIR } else { "$env:USERPROFILE\Documents\Brain" }))

$ErrorActionPreference = "Stop"
if ($ExecutionContext.SessionState.LanguageMode -ne "FullLanguage") {
    Write-Host "  [X]  PowerShell est verrouille sur ce PC (politique de l'entreprise). Demande a ton service informatique, ou utilise un PC perso." -ForegroundColor Red
    throw "brain-in-a-box : PowerShell en mode restreint ($($ExecutionContext.SessionState.LanguageMode))"
}
# Colle par `irm | iex` sur un PC neuf, la politique est Restricted : les modules script de Windows
# (ScheduledTasks...) refusent de se charger. Bypass pour ce processus seulement, sans droits admin.
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force -ErrorAction Stop } catch {}
# Windows PowerShell 5.1 negocie encore TLS 1.0 sur les vieux Windows 10 : GitHub et claude.ai le refusent.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$H = $env:USERPROFILE
$HOOKS = "$H\.claude\hooks\brain"
$BIN = "$H\.local\bin"
$BSDIR = "$H\.brain-search"
$SRC = "$H\.brain-in-a-box\src"

function Say($t)  { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
# throw et pas exit : lance par `irm | iex`, exit fermerait la fenetre avant qu'on lise le message.
function Fail($t) { Write-Host "  [X]  $t" -ForegroundColor Red; throw "brain-in-a-box : installation arretee - $t" }
function Have($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") + ";" + $env:Path
}
# Une commande native qui ecrit sur stderr (avertissement git, progression) devient une erreur
# terminante quand PowerShell capture ce flux (ISE, CI, sortie redirigee) avec Stop. Le code de sortie fait foi.
function Invoke-Native([scriptblock]$Cmd) { $ErrorActionPreference = "Continue"; & $Cmd }

if ($psISE) { Fail "tu es dans PowerShell ISE. Ouvre 'Windows PowerShell' (pas ISE), puis recolle la commande." }

function Winget-Install($id, $label) {
    if (-not (Have winget)) { Warn "$label absent et winget indisponible"; return }
    Write-Host "  ... $label : installation (winget)"
    # Portee utilisateur d'abord (pas de fenetre admin), sinon l'installeur par defaut du paquet.
    foreach ($scope in @("user", "")) {
        $a = @("install", "-e", "--id", $id, "--source", "winget", "--silent", "--accept-package-agreements", "--accept-source-agreements")
        if ($scope) { $a += @("--scope", $scope) }
        Invoke-Native { & winget @a }
        if ($LASTEXITCODE -eq 0) { break }
    }
    Refresh-Path
}

# Le "python" livre avec Windows est un raccourci vers le Microsoft Store : il est sur le PATH mais
# n'execute rien (exit 9009). On ne garde qu'un interpreteur qui fait vraiment tourner du code.
# Le chemin revient en hexa UTF-8 : en clair, Python l'ecrit en ANSI et PowerShell le relit en OEM,
# ce qui abime "C:\Users\Jerome" accentue et ferait rejeter un Python valide.
function Test-Python($exe, [string[]]$pre = @()) {
    $ErrorActionPreference = "Continue"
    try {
        $out = & $exe @pre -c "import sys; assert sys.version_info >= (3, 8); print(sys.executable.encode('utf-8').hex())" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $hex = ([string]($out | Select-Object -Last 1)).Trim()
            $bytes = [byte[]]@(for ($i = 0; $i -lt $hex.Length; $i += 2) { [Convert]::ToByte($hex.Substring($i, 2), 16) })
            $p = [Text.Encoding]::UTF8.GetString($bytes)
            if ($p -and (Test-Path -LiteralPath $p)) { return $p }
        }
    } catch {}   # interpreteur absent ou casse : candidat suivant
    return $null
}

function Find-Python {
    $cands = @()
    foreach ($n in "python", "python3") {
        $cands += @(Get-Command $n -All -CommandType Application -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
    }
    $cands += @(Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    foreach ($c in $cands) { $p = Test-Python $c; if ($p) { return $p } }
    $launcher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) { return (Test-Python $launcher.Source @("-3")) }
    return $null
}

function Find-Git {
    $g = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($g) { return $g.Source }
    foreach ($p in "$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

function Find-Claude {
    $c = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    if (Test-Path -LiteralPath "$BIN\claude.exe") { return "$BIN\claude.exe" }
    return $null
}

function Find-Obsidian {
    foreach ($p in "$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe", "$env:LOCALAPPDATA\Obsidian\Obsidian.exe", "$env:ProgramFiles\Obsidian\Obsidian.exe") {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# Les .cmd sont lus dans la page de code OEM : un chemin avec accent (C:\Users\Jerome accentue) y casse.
# On remplace le debut du chemin par sa variable d'environnement quand c'est possible.
function To-CmdPath($p) {
    foreach ($v in "LOCALAPPDATA", "USERPROFILE") {
        $root = [Environment]::GetEnvironmentVariable($v)
        if ($root -and $p.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return "%$v%" + $p.Substring($root.Length) }
    }
    return $p
}

# 0. Sources : clone local, ou telechargement quand le script arrive par `irm | iex`
$REPO = $null
if ($PSCommandPath) { $REPO = Split-Path -Parent $PSCommandPath }
if (-not $REPO -or -not (Test-Path -LiteralPath (Join-Path $REPO "vault-skeleton"))) {
    Say "Telechargement de brain-in-a-box"
    $archive = if ($env:BIAB_ARCHIVE) { $env:BIAB_ARCHIVE } else { "https://github.com/BrainInBox/brain-in-a-box/archive/refs/heads/master.zip" }
    $dl = Join-Path $env:TEMP ("brain-in-a-box-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $dl -Force | Out-Null
    $zip = Join-Path $dl "src.zip"
    if (Test-Path -LiteralPath $archive) {
        Copy-Item -LiteralPath $archive $zip
    } else {
        $pp = $ProgressPreference; $ProgressPreference = "SilentlyContinue"   # la barre de progression divise le debit par 10 sous 5.1
        try { Invoke-WebRequest -Uri $archive -OutFile $zip -UseBasicParsing }
        catch { Fail "telechargement impossible ($archive) : $($_.Exception.Message). Verifie ta connexion puis relance." }
        finally { $ProgressPreference = $pp }
    }
    # .NET plutot qu'Expand-Archive : pas de module script a charger, quelle que soit la politique d'execution.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $dl)
    $top = Get-ChildItem -LiteralPath $dl -Directory | Select-Object -First 1
    if (-not $top -or -not (Test-Path -LiteralPath (Join-Path $top.FullName "vault-skeleton"))) { Fail "archive inattendue ($archive)" }
    if (Test-Path -LiteralPath $SRC) { Remove-Item -LiteralPath $SRC -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path -Parent $SRC) -Force | Out-Null
    Copy-Item -LiteralPath $top.FullName -Destination $SRC -Recurse
    Remove-Item -LiteralPath $dl -Recurse -Force -ErrorAction SilentlyContinue
    $REPO = $SRC
    Ok "sources dans $SRC"
}

# 1. Preflight : Python (obligatoire), Git (historique du vault, et Git Bash pour l'outil Bash de Claude Code)
Say "Preflight"
$Py = Find-Python
if (-not $Py) { Winget-Install "Python.Python.3.12" "Python"; $Py = Find-Python }
if (-not $Py) { Fail "Python introuvable. Installe Python 3 depuis https://www.python.org/downloads/ puis relance la meme commande." }
Ok "python ($Py)"
$Git = Find-Git
if (-not $Git) { Winget-Install "Git.Git" "Git"; $Git = Find-Git }
if ($Git) {
    $gitDir = Split-Path -Parent $Git
    if (($env:Path -split ";") -notcontains $gitDir) { $env:Path = "$gitDir;$env:Path" }
    Ok "git ($Git)"
} else { Warn "Git absent : vault non versionne, et Claude utilisera PowerShell au lieu de Bash. Installe Git (https://git-scm.com) puis relance." }

# 2. Vault skeleton (jamais ecrase)
Say "Brain vault ($BrainDir)"
$hasContent = (Test-Path -LiteralPath $BrainDir) -and (@(Get-ChildItem -LiteralPath $BrainDir -Force -ErrorAction SilentlyContinue).Count -gt 0)
if ($hasContent) {
    Warn "vault deja present -> skeleton NON copie (on garde ton contenu)"
} else {
    try {
        New-Item -ItemType Directory -Path $BrainDir -Force | Out-Null
        Copy-Item "$REPO\vault-skeleton\*" $BrainDir -Recurse -Force
    } catch {
        Fail "impossible d'ecrire dans $BrainDir ($($_.Exception.Message)). Si la protection contre les ransomwares est active : Securite Windows > Protection contre les virus > Gerer la protection contre les ransomwares > Autoriser une application > ajoute PowerShell, puis relance."
    }
    Ok "skeleton place"
}

# 3. Hooks (cross-platform, simple copie)
Say "Brain hooks ($HOOKS)"
New-Item -ItemType Directory -Path $HOOKS -Force | Out-Null
Copy-Item "$REPO\engine\hooks\*.py" $HOOKS -Force
Ok "hooks copies"

# 4. brain_search (recherche locale) + index + shims gbq
Say "brain_search (recherche locale, sans gbrain)"
New-Item -ItemType Directory -Path $BSDIR -Force | Out-Null
New-Item -ItemType Directory -Path $BIN -Force | Out-Null
Copy-Item "$REPO\engine\search\brain_search.py" "$BSDIR\brain_search.py" -Force
$env:BRAIN_DIR = $BrainDir
Invoke-Native { & $Py "$BSDIR\brain_search.py" index --brain $BrainDir }
if ($LASTEXITCODE -ne 0) { Warn "indexation : echec (code $LASTEXITCODE) - la recherche sera vide jusqu'au reindex de 04:00" }
# gbq.cmd : `gbq query "..."` -> brain_search. sync/embed (commandes gbrain de l'onboarding) -> reindex.
$pyCmd = To-CmdPath $Py
$gbq = "@echo off`r`nsetlocal`r`nset `"BRAIN_DIR=$(To-CmdPath $BrainDir)`"`r`nset `"BS=%USERPROFILE%\.brain-search\brain_search.py`"`r`n" +
       "if /i `"%~1`"==`"sync`" goto reindex`r`nif /i `"%~1`"==`"embed`" goto reindex`r`n" +
       "`"$pyCmd`" `"%BS%`" %*`r`nexit /b %ERRORLEVEL%`r`n:reindex`r`n`"$pyCmd`" `"%BS%`" index`r`nexit /b %ERRORLEVEL%`r`n"
try { $oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage) } catch { $oem = [Text.Encoding]::ASCII }
[IO.File]::WriteAllBytes("$BIN\gbq.cmd", $oem.GetBytes($gbq))
# gbq (sans extension) pour Git Bash, ou tourne l'outil Bash de Claude Code : ~/.local/bin/gbq du CLAUDE.md.
$fwd = { param($p) $p -replace "\\", "/" }
$gbqSh = "#!/bin/sh`n# brain-in-a-box : gbq pour Git Bash, meme logique que gbq.cmd`n" +
         "export BRAIN_DIR=`"`${BRAIN_DIR:-$(& $fwd $BrainDir)}`"`n" +
         "case `"`$1`" in sync|embed) set -- index ;; esac`n" +
         "exec `"$(& $fwd $Py)`" `"$(& $fwd "$BSDIR\brain_search.py")`" `"`$@`"`n"
[IO.File]::WriteAllText("$BIN\gbq", $gbqSh, (New-Object Text.UTF8Encoding $false))
$userPath = [Environment]::GetEnvironmentVariable("PATH","User")
if ($userPath -notlike "*$BIN*") { [Environment]::SetEnvironmentVariable("PATH","$userPath;$BIN","User") }
# Aussi pour ce processus : l'installeur Claude (etape 9) verifie le PATH qu'il herite et sinon
# demande d'aller dans les Proprietes systeme, ce qu'un non-dev ne saura pas faire.
if (($env:Path -split ";") -notcontains $BIN) { $env:Path = "$BIN;$env:Path" }
[Environment]::SetEnvironmentVariable("BRAIN_DIR",$BrainDir,"User")
Ok "gbq installe ($BIN\gbq.cmd + gbq pour Git Bash)"

# 5. settings.json (merge non-destructif des hooks)
Say "Hooks Claude (settings.json)"
$settings = "$H\.claude\settings.json"
$merge = @'
import json, os, sys
settings, pyexe, hookdir = os.path.realpath(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(settings), exist_ok=True)
d = {}
if os.path.exists(settings):
    try:
        with open(settings, encoding="utf-8-sig") as f:  # -sig: Notepad and PowerShell 5.1 write a BOM
            d = json.load(f)
    except Exception as e:
        print("settings.json unreadable, left untouched: %s" % e)
        sys.exit(3)
hooks = d.setdefault("hooks", {})
# Exec form (args): Claude Code spawns python.exe directly. The shell form needs Git Bash; without it
# Claude falls back to PowerShell, which cannot run '"python.exe" "hook.py"'.
def entry(name): return {"type": "command", "command": pyexe, "args": [os.path.join(hookdir, name)]}
def wired(h): return " ".join([str(h.get("command", ""))] + [str(a) for a in h.get("args") or []]).lower()
def ensure(evt, names):
    arr = hooks.setdefault(evt, [])
    grp = next((g for g in arr if g.get("matcher", "") == ""), None)
    if grp is None:
        grp = {"matcher": "", "hooks": []}; arr.append(grp)
    grp.setdefault("hooks", [])
    for n in names:
        target = os.path.join(hookdir, n).lower()
        mine = [(g, h) for g in arr for h in g.get("hooks", []) if target in wired(h)]
        if mine:
            # Already wired (shell form of an older install, another Python): one exec-form entry, current Python.
            h = mine[0][1]
            h.pop("shell", None)
            h.update(entry(n))
            for g, extra in mine[1:]:
                g["hooks"].remove(extra)
                if not g["hooks"] and g is not grp:
                    arr.remove(g)
        else:
            grp["hooks"].append(entry(n))
ensure("UserPromptSubmit", ["correction-detector.py"])
ensure("Stop", ["session-logger.py", "session-indexer.py", "session-recap.py"])
tmp = settings + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
os.replace(tmp, settings)
print("ok settings.json")
'@
# -I : mode isole, le dossier courant n'entre pas dans sys.path (un json.py qui y traine ne serait pas importe).
Invoke-Native { $merge | & $Py -I - $settings $Py $HOOKS }
switch ($LASTEXITCODE) {
    0 { Ok "hooks UserPromptSubmit/Stop cables" }
    3 { Warn "settings.json n'est pas du JSON valide : laisse tel quel, hooks NON cables. Corrige-le puis relance." }
    default { Warn "settings.json : echec (code $LASTEXITCODE), hooks NON cables" }
}

# 6. Global CLAUDE.md (append, jamais ecrase)
Say "CLAUDE.md global"
$GC = "$H\.claude\CLAUDE.md"
$mark = "<!-- brain-in-a-box -->"
$cur = if (Test-Path -LiteralPath $GC) { [IO.File]::ReadAllText($GC) } else { "" }
if ($cur -match [regex]::Escape($mark)) {
    Ok "deja present (skip)"
} else {
    $tpl = Get-Content "$REPO\engine\CLAUDE.global.template.md" -Raw -Encoding utf8
    [IO.File]::AppendAllText($GC, "`r`n$mark`r`n$tpl", (New-Object Text.UTF8Encoding $false))
    Ok "ajoute (ton contenu existant preserve)"
}

# 7. Task Scheduler (reindex nuit + reflection 12h/23h)
Say "Task Scheduler (reindex 04:00 + reflection 12:00/23:00)"
function Register-BrainTask($name, $exe, $argument, $triggers) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $argument
    # LogonType S4U : tourne meme si personne n'est connecte/deverrouille (pas de mot de passe stocke).
    # Le defaut de Register-ScheduledTask -User est LogonType Interactive, qui exige une session active -
    # observe en pratique : echec (0x800710E0, "operateur/administrateur a refuse la requete") au declenchement
    # de nuit/verrouille, succes seulement quand relance manuellement en session active.
    # Certaines editions refusent S4U (Acces refuse rapporte sous Windows Home, PR #6) : on retombe
    # alors sur le mode par defaut plutot que de n'installer aucune tache.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
    try {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Force -ErrorAction Stop | Out-Null
        Ok "tache: $name"
    } catch {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Force -User $env:USERNAME -ErrorAction Stop | Out-Null
        Warn "tache: $name - S4U refuse ($($_.Exception.Message)) ; enregistree en mode session ouverte, elle ne tourne que session deverrouillee"
    }
}
# pythonw = pas de fenetre noire qui s'ouvre a 04:00. La reflection garde python.exe : elle ecrit sur stdout.
$Pyw = Join-Path (Split-Path -Parent $Py) "pythonw.exe"
if (-not (Test-Path -LiteralPath $Pyw)) { $Pyw = $Py }
# Un antislash final avant le guillemet fermant l'echapperait ("D:\Brain\" -> D:\Brain"), on le double.
$bdArg = $BrainDir -replace '\\+$', '$0$0'
try {
    Register-BrainTask "BrainSearchReindex" $Pyw "`"$BSDIR\brain_search.py`" index --brain `"$bdArg`"" (New-ScheduledTaskTrigger -Daily -At "04:00")
    $t12 = New-ScheduledTaskTrigger -Daily -At "12:00"
    $t23 = New-ScheduledTaskTrigger -Daily -At "23:00"
    Register-BrainTask "BrainReflection" $Py "`"$HOOKS\daily-reflection.py`"" @($t12, $t23)
} catch {
    Warn "Task Scheduler : $($_.Exception.Message) - recree les taches a la main si besoin"
}

# 8. git init vault
Say "Historique du vault (git)"
if (-not $Git) {
    Warn "git absent : etape sautee"
} elseif (Test-Path -LiteralPath "$BrainDir\.git") {
    Ok "deja un repo git"
} else {
    Push-Location -LiteralPath $BrainDir
    try {
        if (-not (Test-Path -LiteralPath ".gitignore")) { ".obsidian/`n.DS_Store`n" | Set-Content .gitignore -Encoding ascii }
        Invoke-Native {
            & $Git init -q
            & $Git -c core.safecrlf=false add -A   # sans ca : un avertissement LF/CRLF par fichier
            & $Git -c user.email="brain@local" -c user.name="brain" commit -q --allow-empty -m "brain init"
        }
        if ($LASTEXITCODE -eq 0) { Ok "repo git cree" } else { Warn "git : le premier commit a echoue (code $LASTEXITCODE)" }
    } finally { Pop-Location }
}

# 9. Applis : Claude Code (l'agent) et Obsidian (pour voir ton cerveau)
Say "Claude Code + Obsidian"
if (-not (Find-Claude)) {
    Write-Host "  ... Claude Code : installation (installeur officiel d'Anthropic)"
    # Processus a part : l'installeur officiel peut faire `exit`, ce qui fermerait cette fenetre en mode `irm | iex`.
    $psExe = if ($PSVersionTable.PSEdition -eq "Core") { Join-Path $PSHOME "pwsh.exe" } else { Join-Path $PSHOME "powershell.exe" }
    Invoke-Native { & $psExe -NoProfile -ExecutionPolicy Bypass -Command '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; irm https://claude.ai/install.ps1 | iex' }
    Refresh-Path
}
$Claude = Find-Claude
if ($Claude) { Ok "Claude Code ($Claude)" } else { Warn "Claude Code non installe - telecharge-le sur https://claude.com/download puis relance" }
if (-not (Find-Obsidian)) { Winget-Install "Obsidian.Obsidian" "Obsidian" }
$Obsidian = Find-Obsidian
if ($Obsidian) { Ok "Obsidian ($Obsidian)" } else { Warn "Obsidian non installe - gratuit sur https://obsidian.md" }

# 10. Verifier la recherche
Say "Verification"
Invoke-Native { & $Py "$BSDIR\brain_search.py" health }
if ($LASTEXITCODE -ne 0) { Warn "brain_search health : code $LASTEXITCODE" }

Write-Host ""
Write-Host "brain-in-a-box est installe (Windows natif)." -ForegroundColor Green
Write-Host ""
Write-Host "Prochaines etapes :" -ForegroundColor Cyan
Write-Host "  1. Ouvre un NOUVEAU terminal PowerShell et tape :"
Write-Host "       cd `"$BrainDir`""
Write-Host "       claude"
Write-Host "     La premiere fois, Claude ouvre ton navigateur pour te connecter (compte Pro ou Max)."
Write-Host "  2. Personnalise ton cerveau (15 min) : dans Claude, colle"
Write-Host "       Read $REPO\onboarding\ONBOARDING.md and run it to personalize me."
if ($Obsidian) {
    try { Set-Clipboard -Value $BrainDir; $copied = " (chemin deja copie : Ctrl+V dans la barre d'adresse)" } catch { $copied = "" }
    Write-Host "  3. Obsidian : ouvre-le, 'Open folder as vault', choisis $BrainDir$copied"
    if (([Environment]::GetFolderPath("MyDocuments")).TrimEnd("\") -ne "$H\Documents") {
        Write-Host "     Attention : ton dossier Documents habituel est celui de OneDrive. Ton cerveau n'y est PAS, il est dans $BrainDir." -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Memoire : gbq query `"...`" (recherche locale). Reindex chaque nuit, reflection 12h/23h si Claude est connecte." -ForegroundColor Cyan
Write-Host "Mise a jour : relance la meme commande d'installation." -ForegroundColor Cyan
