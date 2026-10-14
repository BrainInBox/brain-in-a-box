<#
.SYNOPSIS
  brain-in-a-box - installeur Windows natif (second cerveau perso).
  Equivalent de install.sh : la memoire est gbrain (PGLite + pgvector), avec une cle
  Voyage AI optionnelle pour chercher par le sens ; sans cle, par mots-cles.
  Si gbrain ne s'installe pas, la recherche reste assuree par brain_search (BM25 pur
  Python, hors ligne), qui sert aussi de repli quand gbrain ne repond pas.
  Idempotent et non-destructif (ne remplace jamais un vault/CLAUDE.md existant).
  Installe ce qui manque : Python, Git, Bun, Claude Code, Obsidian (sans admin quand c'est possible).

.NOTES
  Scheduler = Task Scheduler (vs launchd). gbq.cmd et gbq (Git Bash) passent par
  engine/search/gbq.py : gbrain, ou brain_search en repli. CLAUDE.md inchange.
  Une installation neuve de gbrain part de la version testee en CI (engine\gbrain-pin.json). Ensuite la
  tache de nuit le met a jour (gbrain_update.py : version publiee depuis au moins 7 jours, jamais plus bas).
  La tache de nuit fait aussi le controle du cerveau (controle.py, un commit par correction, annulable) ;
  controle.cmd et controle (Git Bash) le lancent a la demande, pour le skill /controle.
  Pre-requis : Windows 10 (1809+) ou 11. Le reste s'installe tout seul.
  Lance par `irm | iex`, le script telecharge le repo dans ~\.brain-in-a-box\src
  (BIAB_ARCHIVE = autre archive .zip, URL ou chemin local).
  Cle Voyage : demandee si une console est la, sinon lue dans VOYAGE_API_KEY. Jamais en argument, jamais dans un log.

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
$LOG = "$H\.brain-in-a-box\install.log"
$GREPO = "$H\DEV\gbrain"
$GBRAIN_URL = "https://github.com/garrytan/gbrain.git"
$GDATA = Join-Path $(if ($env:GBRAIN_HOME) { $env:GBRAIN_HOME.Trim() } else { $H }) ".gbrain"
$BUNBIN = if ($env:BUN_INSTALL) { "$env:BUN_INSTALL\bin" } else { "$H\.bun\bin" }

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
# Elle tourne sans VOYAGE_API_KEY (winget, installeurs, git, Python) : seul gbrain s'en sert, comme
# `env -u VOYAGE_API_KEY` dans install.sh.
function Invoke-Native([scriptblock]$Cmd) {
    $ErrorActionPreference = "Continue"
    $biabEnvKey = $env:VOYAGE_API_KEY
    if ($biabEnvKey) { Remove-Item Env:VOYAGE_API_KEY }
    try { & $Cmd } finally { if ($biabEnvKey) { $env:VOYAGE_API_KEY = $biabEnvKey } }
}
# Les cles Voyage connues (saisie, VOYAGE_API_KEY, config.json) : masquees dans le journal, au cas ou un outil les ecrirait.
$KeyMasks = @()
function Hide-Keys([string]$s) {
    foreach ($k in $script:KeyMasks) { if ($k) { $s = $s.Replace($k, "[cle voyage]") } }
    return $s
}
# Un fichier installe qui differe de sa nouvelle version (une version d'avant, ou adaptee sur ce PC)
# est copie dans ~\.brain-in-a-box\backups\<date>\ avant d'etre remplace, comme install.sh.
$BACKUP = "$H\.brain-in-a-box\backups\$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$BackedUp = @()
function Place-File([string]$Dest, [byte[]]$Bytes) {
    if (Test-Path -LiteralPath $Dest -PathType Leaf) {
        $old = [IO.File]::ReadAllBytes($Dest)
        if ([Convert]::ToBase64String($old) -eq [Convert]::ToBase64String($Bytes)) { return }
        $dir = Join-Path $BACKUP (Split-Path -Leaf (Split-Path -Parent $Dest))
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        [IO.File]::WriteAllBytes((Join-Path $dir (Split-Path -Leaf $Dest)), $old)
        $script:BackedUp += $Dest
    }
    [IO.File]::WriteAllBytes($Dest, $Bytes)
}

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

# Lanceurs Git Bash : un chemin entre apostrophes, ' ecrit '\'' . Un profil nomme a$HOME, avec un
# accent grave ou une accolade, ne lance rien et reste un seul mot.
function ShQuote([string]$s) { return "'" + ($s -replace "'", "'\''") + "'" }

# Quelqu'un devant une console peut repondre a une question. Lance par une appli, la CI
# ou avec -NonInteractive, l'entree est redirigee : pas de question qui bloquerait.
function Test-Interactive {
    if (-not [Environment]::UserInteractive) { return $false }
    if (@([Environment]::GetCommandLineArgs() | Where-Object { $_ -match '^-noni' }).Count) { return $false }
    try { return -not [Console]::IsInputRedirected } catch { return $false }
}

# Lance un outil natif, sa sortie va dans le journal d'installation (pas a l'ecran). Les
# arguments y sont recopies : une cle ne passe jamais par la, seulement par l'environnement.
# Hors gbrain (installeur de Bun, scripts des dependances de bun install, git), sans VOYAGE_API_KEY.
# Renvoie le code de sortie ; la sortie reste lisible dans $script:LastOut.
function Invoke-Logged([string]$Exe, [string[]]$ArgList, [string]$Dir = "") {
    $ErrorActionPreference = "Continue"
    Add-Content -LiteralPath $LOG -Value (Hide-Keys "`r`n> $(Split-Path -Leaf $Exe) $($ArgList -join ' ')") -Encoding utf8
    if ($Dir) { Push-Location -LiteralPath $Dir }
    $prev = $null
    try { $prev = [Console]::OutputEncoding; [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}   # gbrain ecrit en UTF-8
    $envKey = $env:VOYAGE_API_KEY
    $strip = $envKey -and ((Split-Path -Leaf $Exe) -notlike "gbrain*")
    try {
        if ($strip) { Remove-Item Env:VOYAGE_API_KEY }
        $out = @(& $Exe @ArgList 2>&1 | ForEach-Object { Hide-Keys "$_" })
        $code = $LASTEXITCODE
    } catch {
        $out = @(Hide-Keys "$($_.Exception.Message)"); $code = 1
    } finally {
        if ($strip) { $env:VOYAGE_API_KEY = $envKey }
        if ($prev) { try { [Console]::OutputEncoding = $prev } catch {} }
        if ($Dir) { Pop-Location }
    }
    if ($out.Count) { Add-Content -LiteralPath $LOG -Value $out -Encoding utf8 }
    $script:LastOut = $out -join "`n"
    return $code
}

function Read-GbrainConfig {
    $p = Join-Path $GDATA "config.json"
    if (-not (Test-Path -LiteralPath $p)) { return $null }
    try { return ([IO.File]::ReadAllText($p) | ConvertFrom-Json) } catch { return $null }
}

# La verification d'install.sh : une requete d'un token, la cle dans un en-tete de ce processus.
# .NET plutot qu'Invoke-WebRequest : la journalisation des modules PowerShell (GPO d'entreprise)
# enregistre les parametres des cmdlets, en-tete Authorization compris.
function Test-VoyageKey([string]$Key, [string]$Base) {
    try {
        $req = [Net.WebRequest]::Create("$Base/embeddings")
        $req.Method = "POST"
        $req.ContentType = "application/json"
        $req.Timeout = 20000
        $req.Headers.Add("Authorization", "Bearer $Key")
        $body = [Text.Encoding]::UTF8.GetBytes('{"input":["ok"],"model":"voyage-4"}')
        $stream = $req.GetRequestStream()
        try { $stream.Write($body, 0, $body.Length) } finally { $stream.Close() }
        $resp = $req.GetResponse()
        try { return ([int]$resp.StatusCode -eq 200) } finally { $resp.Close() }
    } catch { return $false }
}

# "v0.60.210.0" ou "0.60.212" -> [version] a 4 nombres (0 si absent), comme gbrain_update.py ; $null sinon.
function Get-GbrainVersion([string]$Text) {
    if ($Text -cmatch '^v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.([0-9]+))?$') {
        try { return [version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]("0" + $Matches[4])) } catch {}
    }
    return $null
}

function Get-BunVersion([string]$Bun) {
    $ErrorActionPreference = "Continue"
    if (-not (Test-Path -LiteralPath $Bun)) { return $null }
    try { return [version](([string](& $Bun --version 2>$null | Select-Object -First 1)).Trim() -replace '[-+].*$', '') } catch { return $null }
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

# La version de gbrain d'une installation neuve et sa version de Bun : celles que le job windows de la CI
# installe et teste. On les monte ensemble dans engine\gbrain-pin.json, par une PR dont la CI passe.
try { $pin = [IO.File]::ReadAllText("$REPO\engine\gbrain-pin.json") | ConvertFrom-Json } catch { $pin = $null }
# Valeurs verifiees : $BUN_VERSION entre dans une commande PowerShell (installeur de Bun).
if (-not $pin -or "$($pin.tag)" -cnotmatch '^v[0-9]+(\.[0-9]+){2,3}$' -or "$($pin.sha)" -cnotmatch '^[0-9a-f]{40}$' -or "$($pin.bun)" -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    Fail "engine\gbrain-pin.json illisible. Retelecharge brain-in-a-box puis relance."
}
$GBRAIN_TAG = [string]$pin.tag
$GBRAIN_REF = [string]$pin.sha
$BUN_VERSION = [string]$pin.bun

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
New-Item -ItemType Directory -Path (Split-Path -Parent $LOG) -Force | Out-Null
Add-Content -LiteralPath $LOG -Value "`r`n===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') install =====" -Encoding utf8

# 1bis. Recherche par le sens : cle Voyage AI (optionnelle). Un cerveau qui a deja ses
# embeddings les garde. Sans cle, la recherche marche par mots-cles ; relancer pour en ajouter une.
Say "Recherche par le sens (cle Voyage AI, optionnelle)"
$cfg = Read-GbrainConfig
$EmbedModel = if ($cfg -and $cfg.embedding_model -and -not $cfg.embedding_disabled) { [string]$cfg.embedding_model } else { "" }
# provider_base_urls.voyage (un proxy) : la cle est verifiee la ou gbrain l'utilisera.
$VoyageBase = if ($cfg -and $cfg.provider_base_urls -and $cfg.provider_base_urls.voyage) { ([string]$cfg.provider_base_urls.voyage).TrimEnd("/") } else { "https://api.voyageai.com/v1" }
$VoyageKey = ""
if ($EmbedModel) {
    Ok "deja configuree ($EmbedModel), on n'y touche pas"
} elseif ($env:VOYAGE_API_KEY) {
    $VoyageKey = $env:VOYAGE_API_KEY.Trim()
    Ok "cle prise dans VOYAGE_API_KEY"
} elseif (Test-Interactive) {
    Write-Host "  Ton cerveau retrouve tes notes par mots-cles des maintenant. Pour les retrouver"
    Write-Host "  aussi par le sens, il utilise Voyage AI (5 minutes) :"
    Write-Host "    1. Cree un compte sur https://dashboard.voyageai.com"
    Write-Host "    2. Ajoute une carte (Organization > Billing). Sans carte, Voyage n'accepte que"
    Write-Host "       3 requetes par minute : trop lent. 200 millions de tokens sont offerts et un"
    Write-Host "       cerveau perso en utilise bien moins de 1 % ; au-dela, Voyage facture au mois."
    Write-Host "    3. Cree une cle API (elle commence par pa-) et colle-la ci-dessous (clic droit pour coller)."
    Write-Host "  Entree pour passer. Relance la meme commande d'installation pour l'ajouter plus tard."
    try {
        $ss = Read-Host "  Cle Voyage" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
        try { $VoyageKey = ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    } catch { $VoyageKey = "" }
} else {
    Ok "pas de console : recherche par mots-cles pour l'instant (relance avec VOYAGE_API_KEY pour l'ajouter)"
}
$ss = $null
$KeyMasks = @(@($VoyageKey, "$env:VOYAGE_API_KEY".Trim(), $(if ($cfg) { [string]$cfg.voyage_api_key })) | Where-Object { $_ })
if ($VoyageKey) {
    if (Test-VoyageKey $VoyageKey $VoyageBase) {
        Ok "cle acceptee par Voyage"
    } else {
        Warn "Voyage n'a pas accepte cette cle (cle fausse, ou pas de reseau) : recherche par mots-cles pour l'instant. Corrige-la, puis relance la meme commande."
        $VoyageKey = ""
    }
}

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

# 3. Hooks (cross-platform ; une version differente est sauvegardee avant d'etre remplacee)
Say "Brain hooks ($HOOKS)"
New-Item -ItemType Directory -Path $HOOKS -Force | Out-Null
foreach ($f in @(Get-ChildItem -LiteralPath "$REPO\engine\hooks" -Filter *.py -File) + @(Get-Item -LiteralPath "$REPO\engine\nightly\windows-nightly.py")) {
    Place-File (Join-Path $HOOKS $f.Name) ([IO.File]::ReadAllBytes($f.FullName))
}
Ok "hooks copies"

# 4. brain_search (mots-cles, hors ligne : le repli quand gbrain manque ou ne repond pas) + shims gbq
Say "Recherche par mots-cles (brain_search, toujours la en repli)"
New-Item -ItemType Directory -Path $BSDIR -Force | Out-Null
New-Item -ItemType Directory -Path $BIN -Force | Out-Null
foreach ($n in "brain_search.py", "gbq.py") { Place-File "$BSDIR\$n" ([IO.File]::ReadAllBytes("$REPO\engine\search\$n")) }
$env:BRAIN_DIR = $BrainDir
Invoke-Native { & $Py "$BSDIR\brain_search.py" index --brain $BrainDir }
if ($LASTEXITCODE -ne 0) { Warn "indexation : echec (code $LASTEXITCODE) - la recherche sera vide jusqu'au reindex de 04:00" }
# gbq.cmd et gbq (Git Bash) -> gbq.py : gbrain quand il est installe, brain_search sinon.
$pyCmd = To-CmdPath $Py
$gbq = "@echo off`r`nsetlocal`r`nset `"BRAIN_DIR=$(To-CmdPath $BrainDir)`"`r`n" +
       "`"$pyCmd`" `"$(To-CmdPath "$BSDIR\gbq.py")`" %*`r`nexit /b %ERRORLEVEL%`r`n"
try { $oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage) } catch { $oem = [Text.Encoding]::ASCII }
Place-File "$BIN\gbq.cmd" $oem.GetBytes($gbq)
# gbq (sans extension) pour Git Bash, ou tourne l'outil Bash de Claude Code : ~/.local/bin/gbq du CLAUDE.md.
$fwd = { param($p) $p -replace "\\", "/" }
$gbqSh = "#!/bin/sh`n# brain-in-a-box : gbq pour Git Bash, meme logique que gbq.cmd`n" +
         "[ -n `"`$BRAIN_DIR`" ] || BRAIN_DIR=$(ShQuote (& $fwd $BrainDir))`nexport BRAIN_DIR`n" +
         "exec $(ShQuote (& $fwd $Py)) $(ShQuote (& $fwd "$BSDIR\gbq.py")) `"`$@`"`n"
Place-File "$BIN\gbq" (New-Object Text.UTF8Encoding $false).GetBytes($gbqSh)
# controle.cmd et controle (Git Bash) -> controle.py : le controle de nuit a la demande (skill /controle).
$ctl = "@echo off`r`nsetlocal`r`nset `"BRAIN_DIR=$(To-CmdPath $BrainDir)`"`r`n" +
       "`"$pyCmd`" -I `"$(To-CmdPath "$HOOKS\controle.py")`" %*`r`nexit /b %ERRORLEVEL%`r`n"
Place-File "$BIN\controle.cmd" $oem.GetBytes($ctl)
$ctlSh = "#!/bin/sh`n# brain-in-a-box : controle pour Git Bash, meme logique que controle.cmd`n" +
         "[ -n `"`$BRAIN_DIR`" ] || BRAIN_DIR=$(ShQuote (& $fwd $BrainDir))`nexport BRAIN_DIR`n" +
         "exec $(ShQuote (& $fwd $Py)) -I $(ShQuote (& $fwd "$HOOKS\controle.py")) `"`$@`"`n"
Place-File "$BIN\controle" (New-Object Text.UTF8Encoding $false).GetBytes($ctlSh)
$userPath = [Environment]::GetEnvironmentVariable("PATH","User")
if ($userPath -notlike "*$BIN*") { [Environment]::SetEnvironmentVariable("PATH","$userPath;$BIN","User") }
# Aussi pour ce processus : l'installeur Claude (etape 10) verifie le PATH qu'il herite et sinon
# demande d'aller dans les Proprietes systeme, ce qu'un non-dev ne saura pas faire.
if (($env:Path -split ";") -notcontains $BIN) { $env:Path = "$BIN;$env:Path" }
[Environment]::SetEnvironmentVariable("BRAIN_DIR",$BrainDir,"User")
Ok "gbq installe ($BIN\gbq.cmd + gbq pour Git Bash)"
Ok "controle installe ($BIN\controle.cmd + controle pour Git Bash)"

# 5. settings.json (merge non-destructif des hooks)
Say "Hooks Claude (settings.json)"
$settings = "$H\.claude\settings.json"
$merge = @'
import json, os, sys, time
settings, pyexe, hookdir = os.path.realpath(sys.argv[1]), sys.argv[2], sys.argv[3]
os.makedirs(os.path.dirname(settings), exist_ok=True)
raw, d = None, {}
if os.path.exists(settings):
    try:
        with open(settings, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8-sig")  # -sig: Notepad and PowerShell 5.1 write a BOM
        d = json.loads(text) if text.strip() else {}
    except Exception as e:
        print("settings.json unreadable, left untouched: %s" % e)
        sys.exit(3)
before = json.dumps(d, sort_keys=True)
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
ensure("SessionStart", ["gbrain-update-check.py", "controle-sessionstart.py"])
if json.dumps(d, sort_keys=True) == before:
    print("ok settings.json (unchanged)")
    sys.exit(0)
if raw is not None:  # the previous version stays next to it, as install.sh does
    with open("%s.bak-%s" % (settings, time.strftime("%Y%m%d-%H%M%S")), "wb") as f:
        f.write(raw)
tmp = settings + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
os.replace(tmp, settings)
print("ok settings.json")
'@
# -I : mode isole, le dossier courant n'entre pas dans sys.path (un json.py qui y traine ne serait pas importe).
Invoke-Native { $merge | & $Py -I - $settings $Py $HOOKS }
switch ($LASTEXITCODE) {
    0 { Ok "hooks UserPromptSubmit/Stop/SessionStart cables" }
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

# 6bis. Skills Claude Code (jamais un skill a toi ecrase : voir engine/install_skills.py)
Say "Skills Claude Code ($H\.claude\skills)"
Invoke-Native { & $Py -I "$REPO\engine\install_skills.py" --src "$REPO\skills" --dest "$H\.claude\skills" }
if ($LASTEXITCODE -eq 0) { Ok "skills a jour" } else { Warn "skills : echec (code $LASTEXITCODE)" }

# 7. git init vault (gbrain lit le vault a travers git)
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
            # Git pour Windows reecrit les fins de ligne a chaque checkout (core.autocrlf=true) :
            # annuler un controle passerait des notes LF en CRLF. Le vault garde ses octets.
            & $Git config core.autocrlf false
            & $Git -c core.safecrlf=false add -A   # sans ca : un avertissement LF/CRLF par fichier
            & $Git -c user.email="brain@local" -c user.name="brain" commit -q --allow-empty -m "brain init"
        }
        if ($LASTEXITCODE -eq 0) { Ok "repo git cree" } else { Warn "git : le premier commit a echoue (code $LASTEXITCODE)" }
    } finally { Pop-Location }
}

# 8. gbrain, la meme memoire que sur Mac : Bun + gbrain (PGLite + pgvector) + le vault comme source.
# Chaque echec laisse la recherche par mots-cles en place : gbq passe alors par brain_search.
Say "Memoire gbrain (2 a 5 minutes la premiere fois)"
function Install-Gbrain {
    if (-not $Git) { Warn "gbrain a besoin de Git"; return $false }

    # Bun, le moteur de gbrain : installeur officiel, par utilisateur, la version testee en CI.
    $bun = Join-Path $BUNBIN "bun.exe"
    $have = Get-BunVersion $bun
    if (-not $have -or $have -lt [version]$BUN_VERSION) {
        Write-Host "  ... Bun $BUN_VERSION : installation (installeur officiel, sans droits admin)"
        # Processus a part, comme pour Claude Code. -NoCompletions : ton profil PowerShell reste tel quel.
        $psExe = if ($PSVersionTable.PSEdition -eq "Core") { Join-Path $PSHOME "pwsh.exe" } else { Join-Path $PSHOME "powershell.exe" }
        $cmd = "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; " +
               "& ([scriptblock]::Create((Invoke-RestMethod https://bun.sh/install.ps1))) -Version $BUN_VERSION -NoCompletions"
        $null = Invoke-Logged $psExe @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $cmd)
        $have = Get-BunVersion $bun
    }
    if (-not $have) { Warn "Bun ne s'est pas installe (details : $LOG)"; return $false }
    if (($env:Path -split ";") -notcontains $BUNBIN) { $env:Path = "$BUNBIN;$env:Path" }
    Ok "bun $have"

    # Le commit teste, recupere seul : 46 Mo au lieu de 600 avec tout l'historique.
    $fresh = $false
    if (-not (Test-Path -LiteralPath "$GREPO\.git")) {
        if ((Test-Path -LiteralPath $GREPO) -and @(Get-ChildItem -LiteralPath $GREPO -Force).Count) {
            Warn "$GREPO existe deja et n'est pas un clone de gbrain : on n'y touche pas"
            return $false
        }
        New-Item -ItemType Directory -Path $GREPO -Force | Out-Null
        if ((Invoke-Logged $Git @("init", "-q", $GREPO)) -ne 0) { Warn "git init $GREPO a echoue (details : $LOG)"; return $false }
        $fresh = $true
    }
    # Jamais plus bas : un gbrain a la version epinglee ou plus loin (la tache de nuit l'a mis a jour,
    # sa base a peut-etre ete migree) reste ou il est. Sur un clone superficiel, merge-base ne le voit pas.
    $installed = ""
    if (-not $fresh) {
        $pj = Invoke-Native { & $Git -C $GREPO show HEAD:package.json 2>$null }
        if ($LASTEXITCODE -eq 0) { try { $installed = [string](($pj -join "`n") | ConvertFrom-Json).version } catch {} }
    }
    $iv = Get-GbrainVersion $installed
    if ($iv -and $iv -ge (Get-GbrainVersion $GBRAIN_TAG)) {
        Ok "gbrain deja a jour (v$installed)"
    } elseif (-not $fresh -and (Invoke-Logged $Git @("-C", $GREPO, "merge-base", "--is-ancestor", $GBRAIN_REF, "HEAD")) -eq 0) {
        # Deja sur ce commit, ou plus loin (un clone de dev a jour) : rien a telecharger.
        Ok "gbrain deja a jour"
    } else {
        Write-Host "  ... gbrain : telechargement"
        $depth = @()
        if ($fresh -or ((Invoke-Logged $Git @("-C", $GREPO, "rev-parse", "--is-shallow-repository")) -eq 0 -and $script:LastOut.Trim() -eq "true")) { $depth = @("--depth", "1") }
        # Un depot introuvable ferait demander un identifiant GitHub, cache derriere le journal : on echoue tout de suite.
        $gtp = $env:GIT_TERMINAL_PROMPT
        $env:GIT_TERMINAL_PROMPT = "0"
        try { $rc = Invoke-Logged $Git (@("-C", $GREPO, "-c", "credential.helper=", "fetch", "-q") + $depth + @($GBRAIN_URL, $GBRAIN_REF)) }
        finally { if ($null -eq $gtp) { Remove-Item Env:GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue } else { $env:GIT_TERMINAL_PROMPT = $gtp } }
        if ($rc -ne 0) {
            Warn "gbrain : telechargement impossible, reseau ? (details : $LOG)"
            if ($fresh) { Remove-Item -LiteralPath $GREPO -Recurse -Force -ErrorAction SilentlyContinue }
            return $false
        }
        if ((Invoke-Logged $Git @("-C", $GREPO, "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD")) -ne 0) {
            if ($fresh) { Warn "gbrain : checkout impossible (details : $LOG)"; return $false }
            Warn "gbrain : $GREPO a des modifications locales, on garde sa version"
        }
    }
    Write-Host "  ... gbrain : installation (1 a 3 minutes)"
    # --frozen-lockfile : exactement les dependances que nomme le fichier de verrouillage de gbrain.
    # Un clone d'une ancienne installation peut etre sur un master dont le verrou est en retard :
    # il garde l'installation simple d'avant, jusqu'a ce que la nuit le passe sur une version publiee.
    if ((Invoke-Logged $bun @("install", "--frozen-lockfile") $GREPO) -ne 0) {
        if ($fresh) { Warn "gbrain : bun install a echoue (details : $LOG)"; return $false }
        Warn "gbrain : le verrou des dependances ne correspond pas, installation simple"
        if ((Invoke-Logged $bun @("install") $GREPO) -ne 0) { Warn "gbrain : bun install a echoue (details : $LOG)"; return $false }
    }
    if ((Invoke-Logged $bun @("link") $GREPO) -ne 0) { Warn "gbrain : bun link a echoue (details : $LOG)"; return $false }
    $gb = Join-Path $BUNBIN "gbrain.exe"
    if ((Invoke-Logged $gb @("--version")) -ne 0) { Warn "gbrain ne demarre pas (details : $LOG)"; return $false }
    Ok "$((($script:LastOut -split "`n") | Select-Object -First 1).Trim())"

    # Le cerveau : ~\.gbrain\brain.pglite. Avec une cle acceptee, recherche par le sens (meme pour
    # un cerveau deja la : il passe des mots-cles au sens) ; sinon, mots-cles.
    New-Item -ItemType Directory -Path $GDATA -Force | Out-Null
    $pglite = Test-Path -LiteralPath (Join-Path $GDATA "brain.pglite")
    $model = ""; $dims = ""
    if ($VoyageKey) {
        # Comme install.sh : la cle va dans config.json (dans ton profil), par l'environnement de Python, jamais en argument.
        $setKey = @'
import json, os, sys
p = os.path.join(sys.argv[1], "config.json")
d = {}
if os.path.exists(p):
    with open(p, encoding="utf-8-sig") as f:
        d = json.load(f)
d["voyage_api_key"] = os.environ["BIAB_VOYAGE_KEY"]
with open(p + ".tmp", "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2)
os.replace(p + ".tmp", p)
'@
        $env:BIAB_VOYAGE_KEY = $VoyageKey
        try { Invoke-Native { $setKey | & $Py -I - $GDATA } } finally { Remove-Item Env:BIAB_VOYAGE_KEY -ErrorAction SilentlyContinue }
        if ($LASTEXITCODE -eq 0) { $model = "voyage:voyage-4"; $dims = "1024" } else { Warn "cle Voyage : $GDATA\config.json illisible, recherche par mots-cles" }
    } elseif ($EmbedModel -and -not $pglite) {
        $model = $EmbedModel
        if ($cfg.embedding_dimensions) { $dims = [string]$cfg.embedding_dimensions }
    }
    $init = @("init", "--pglite", "--non-interactive")
    if (-not $pglite) { $init += @("--search-mode", "balanced") }
    if ($model) {
        $init += @("--embedding-model", $model)
        if ($dims) { $init += @("--embedding-dimensions", $dims) }
        Write-Host "  ... cerveau : initialisation (recherche par le sens)"
        if ((Invoke-Logged $gb $init) -eq 0) {
            Ok "cerveau initialise (Voyage : recherche par le sens)"
        } elseif ($pglite) {
            Warn "passage a la recherche par le sens : echec (details : $LOG). Le cerveau garde la recherche par mots-cles."
            $model = ""
        } else { Warn "gbrain init a echoue (details : $LOG)"; return $false }
    } elseif (-not $pglite) {
        Write-Host "  ... cerveau : initialisation"
        if ((Invoke-Logged $gb ($init + "--no-embedding")) -eq 0) { Ok "cerveau initialise (recherche par mots-cles)" }
        else { Warn "gbrain init a echoue (details : $LOG)"; return $false }
    } else {
        Ok "cerveau deja initialise ($(if ($EmbedModel) { $EmbedModel } else { 'recherche par mots-cles' }))"
    }
    # rerank-3-lite reste dans les tokens gratuits de Voyage ; le defaut de gbrain (rerank-2.5) est facture des la 1re recherche.
    if ($VoyageKey -and $model) { $null = Invoke-Logged $gb @("config", "set", "search.reranker.model", "voyage:rerank-3-lite") }
    $null = Invoke-Logged $gb @("config", "set", "search.mode", "balanced")
    # Sans ca, les [[liens]] entre dossiers du squelette (Team/, Decisions/...) sont ignores : voir install.sh.
    $null = Invoke-Logged $gb @("config", "set", "link_resolution.global_basename", "true")
    # La version est choisie par la mise a jour a 7 jours de la tache de nuit : `gbrain upgrade` irait sur master.
    $null = Invoke-Logged $gb @("config", "set", "self_upgrade.mode", "off")

    # gbrain lit le vault a travers git : une note pas encore commitee est invisible.
    if ((Invoke-Logged $Git @("-C", $BrainDir, "status", "--porcelain")) -eq 0 -and $script:LastOut.Trim()) {
        $null = Invoke-Logged $Git @("-C", $BrainDir, "-c", "core.safecrlf=false", "add", "-A")
        $null = Invoke-Logged $Git @("-C", $BrainDir, "-c", "user.email=brain@local", "-c", "user.name=brain", "commit", "-q", "-m", "brain-in-a-box: notes avant indexation")
    }
    # Le vault est une source a part : la source 'default' d'un cerveau neuf est un dossier de gbrain, pas ton vault.
    $null = Invoke-Logged $gb @("sources", "list")
    if ($script:LastOut -match '(?m)^\s+vault\s') {
        Ok "vault deja enregistre comme source"
    } elseif ((Invoke-Logged $gb @("sources", "add", "vault", "--path", $BrainDir, "--federated")) -eq 0) {
        Ok "vault enregistre comme source"
    } else { Warn "gbrain sources add a echoue (details : $LOG)"; return $false }
    Write-Host "  ... cerveau : indexation du vault"
    if ((Invoke-Logged $gb @("sync", "--source", "vault", "--no-pull", "--no-embed")) -ne 0) { Warn "gbrain sync a echoue (details : $LOG)"; return $false }
    Ok "vault indexe"
    if ($model -or $EmbedModel) {
        Write-Host "  ... recherche par le sens : calcul des embeddings (1 a 2 minutes)"
        if ((Invoke-Logged $gb @("embed", "--stale", "--max-usd", "1")) -eq 0) { Ok "embeddings a jour" }
        else { Warn "embeddings pas termines (pas encore de carte sur Voyage ? sans carte, 3 requetes par minute). La nuit les rattrapera." }
    }
    return $true
}
$prevEngine = if (Test-Path -LiteralPath "$BSDIR\engine") { ([IO.File]::ReadAllText("$BSDIR\engine")).Trim() } else { "" }
$GbrainOk = $false
try { $GbrainOk = [bool](Install-Gbrain | Select-Object -Last 1) } catch { Warn "gbrain : $(Hide-Keys $_.Exception.Message) (details : $LOG)" }
# Lance par `irm | iex`, le script tourne dans la session de l'utilisateur : la cle n'y reste pas.
$VoyageKey = $null; $KeyMasks = @()
# gbq.py et la tache de nuit lisent ce fichier. Un gbrain qui marchait reste en place si cette
# relance echoue (pas de reseau...) : gbq repassera de toute facon par les mots-cles s'il ne repond pas.
$Engine = "brain_search"
if ($GbrainOk) {
    $Engine = "gbrain"
} elseif ($prevEngine -eq "gbrain" -and (Test-Path -LiteralPath (Join-Path $BUNBIN "gbrain.exe")) -and (Test-Path -LiteralPath (Join-Path $GDATA "brain.pglite"))) {
    $Engine = "gbrain"
    Warn "on garde le gbrain deja installe"
} else {
    Warn "memoire en mode repli : recherche par mots-cles (brain_search). Relance la meme commande pour reessayer gbrain."
}
[IO.File]::WriteAllText("$BSDIR\engine", "$Engine`n")

# 9. Task Scheduler (nuit 04:00 + reflection 12h/23h)
Say "Task Scheduler (nuit 04:00 + reflection 12:00/23:00)"
function Register-BrainTask($name, $exe, $argument, $triggers) {
    $action = New-ScheduledTaskAction -Execute $exe -Argument $argument
    # Un portable eteint ou en veille a 04:00, ou sur batterie : la tache tourne quand meme, au reveil.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    # LogonType S4U : tourne meme si personne n'est connecte/deverrouille (pas de mot de passe stocke).
    # Le defaut de Register-ScheduledTask -User est LogonType Interactive, qui exige une session active -
    # observe en pratique : echec (0x800710E0, "operateur/administrateur a refuse la requete") au declenchement
    # de nuit/verrouille, succes seulement quand relance manuellement en session active.
    # Certaines editions refusent S4U (Acces refuse rapporte sous Windows Home, PR #6) : on retombe
    # alors sur le mode par defaut plutot que de n'installer aucune tache.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
    try {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
        Ok "tache: $name"
    } catch {
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Settings $settings -Force -User $env:USERNAME -ErrorAction Stop | Out-Null
        Warn "tache: $name - S4U refuse ($($_.Exception.Message)) ; enregistree en mode session ouverte, elle ne tourne que session deverrouillee"
    }
}
# pythonw = pas de fenetre noire qui s'ouvre a 04:00. La reflection garde python.exe : elle ecrit sur stdout.
$Pyw = Join-Path (Split-Path -Parent $Py) "pythonw.exe"
if (-not (Test-Path -LiteralPath $Pyw)) { $Pyw = $Py }
# Un antislash final avant le guillemet fermant l'echapperait ("D:\Brain\" -> D:\Brain"), on le double.
$bdArg = $BrainDir -replace '\\+$', '$0$0'
try {
    $gitArg = if ($Git) { " --git `"$Git`"" } else { "" }
    Register-BrainTask "BrainNightly" $Pyw "`"$HOOKS\windows-nightly.py`" --brain `"$bdArg`"$gitArg" (New-ScheduledTaskTrigger -Daily -At "04:00")
    # Avant gbrain, la nuit ne refaisait que l'index par mots-cles : BrainNightly fait les deux.
    # L'ancienne tache ne part qu'une fois la nouvelle en place.
    if ((Get-ScheduledTask -TaskName "BrainNightly" -ErrorAction SilentlyContinue) -and (Get-ScheduledTask -TaskName "BrainSearchReindex" -ErrorAction SilentlyContinue)) {
        Unregister-ScheduledTask -TaskName "BrainSearchReindex" -Confirm:$false
        Ok "ancienne tache BrainSearchReindex remplacee par BrainNightly"
    }
    $t12 = New-ScheduledTaskTrigger -Daily -At "12:00"
    $t23 = New-ScheduledTaskTrigger -Daily -At "23:00"
    Register-BrainTask "BrainReflection" $Py "`"$HOOKS\daily-reflection.py`"" @($t12, $t23)
} catch {
    Warn "Task Scheduler : $($_.Exception.Message) - recree les taches a la main si besoin"
}

# 10. Applis : Claude Code (l'agent) et Obsidian (pour voir ton cerveau)
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

# 11. Verifier la recherche (l'index par mots-cles sert aussi de repli a gbrain)
Say "Verification"
Invoke-Native { & $Py "$BSDIR\brain_search.py" health }
if ($LASTEXITCODE -ne 0) { Warn "brain_search health : code $LASTEXITCODE" }
$cfg = Read-GbrainConfig
$mem = if ($Engine -ne "gbrain") { "par mots-cles (brain_search)" }
       elseif ($cfg -and $cfg.embedding_model -and -not $cfg.embedding_disabled) { "par le sens (gbrain + Voyage AI)" }
       else { "par mots-cles (gbrain ; relance avec une cle Voyage pour chercher par le sens)" }
$cfg = $null   # il contient la cle
Ok "memoire : recherche $mem"

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
Write-Host "Memoire : gbq query `"...`", recherche $mem. Mise a jour de l'index chaque nuit, reflection 12h/23h si Claude est connecte." -ForegroundColor Cyan
Write-Host "Controle de nuit : il corrige seul ce qui est sans risque (annulable), le reste est dans Profile\controle.md. Dans Claude : /controle." -ForegroundColor Cyan
Write-Host "Journal d'installation : $LOG" -ForegroundColor Cyan
Write-Host "Mise a jour : relance la meme commande d'installation." -ForegroundColor Cyan
if ($BackedUp.Count) {
    Write-Host ""
    Write-Host "$($BackedUp.Count) fichier(s) remplace(s) par leur nouvelle version : $(($BackedUp | ForEach-Object { Split-Path -Leaf $_ }) -join ', ')." -ForegroundColor Yellow
    Write-Host "L'ancienne version de chacun est dans $BACKUP (si tu les avais modifies, reprends tes changements depuis la)." -ForegroundColor Yellow
}
