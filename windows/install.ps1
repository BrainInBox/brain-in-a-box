# brain-in-a-box - installeur NATIF WINDOWS (port de install.sh).
# Idempotent + non-destructif sur tes DONNEES (vault, CLAUDE.md, cle ZE, init).
# NB: les fichiers GERES par l'installeur (hooks .py, gbrain-nightly.ps1, .vbs,
#     wrappers .cmd, taches) SONT reecrits a chaque run (c'est voulu : ce sont
#     des artefacts d'install, pas tes donnees).
# ASCII pur volontairement : PowerShell 5.1 lit un .ps1 UTF-8 sans BOM en cp1252
# et casserait sur les accents / caracteres speciaux.
#
# Differences Windows (toutes eprouvees) :
#   - PowerShell au lieu de bash ; pas de launchd -> Planificateur de taches.
#   - Taches lancees via wscript (hote sans console) -> evite le 0xC000013A (CTRL_C)
#     des runs autonomes/verrouilles. S4U non utilise (bloque sur Windows Home).
#   - Hooks Python patches (encoding utf-8, tempfile au lieu de /tmp, %Y-%m-%d...).
#   - Hooks lances via le chemin python.exe complet (pas le launcher 'py', qui
#     peut etre absent -> capture des corrections cassee en silence).
#   - Pas de 'bun link' (shim casse sous Windows) -> wrappers .cmd vers la source.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Company https://github.com/<org>/brain-company
param([string]$Company = "")

# Continue (pas Stop) : sous PS 5.1, la stderr d'un exe natif (bun/git/winget)
# redirigee via 2>&1 devient une ErrorRecord terminante sous Stop, ce qui tuerait
# le script alors que la commande a reussi. Les vrais prerequis sont gardes par Die().
$ErrorActionPreference = "Continue"

function Say($m){ Write-Host "`n> $m" -ForegroundColor Cyan }
function OK($m){  Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "  [!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

$WIN     = $PSScriptRoot
$REPO    = Split-Path -Parent $WIN
$H       = $env:USERPROFILE
$BRAIN   = "$H\Documents\Brain"
$BRAINCO = "$H\Documents\BrainCo"
$HOOKS   = "$H\.claude\hooks\brain"
$BIN     = "$H\.local\bin"
$GREPO   = "$H\DEV\gbrain"
$GDATA   = "$H\.gbrain"
$utf8    = New-Object System.Text.UTF8Encoding $false

# --- 1. Preflight -------------------------------------------------------------
Say "Preflight"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git manquant - installe Git for Windows." }

$PYEXE = (Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -First 1).Source
if (-not $PYEXE) {
    $cand = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path "$($_.FullName)\python.exe" } | Sort-Object Name -Descending | Select-Object -First 1
    if ($cand) { $PYEXE = Join-Path $cand.FullName "python.exe" }
}
if (-not $PYEXE) { Die "Python 3 manquant - installe-le depuis python.org (pas le stub Microsoft Store)." }

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Warn "claude CLI introuvable - installe Claude Code (la reflection 23h en a besoin)." }

$BUN = @("$H\.bun\bin\bun.exe", "$env:APPDATA\npm\node_modules\bun\bin\bun.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $BUN) {
    Warn "bun manquant -> installation (bun.sh)..."
    Invoke-RestMethod https://bun.sh/install.ps1 | Invoke-Expression
    if (Test-Path "$H\.bun\bin\bun.exe") { $BUN = "$H\.bun\bin\bun.exe" }
}
if (-not $BUN) { Die "bun indisponible apres install." }
OK "deps : git, python ($PYEXE), bun ($BUN)"

# --- 2. GBrain (clone + deps ; PAS de bun link) ------------------------------
Say "GBrain"
New-Item -ItemType Directory -Force -Path "$H\DEV" | Out-Null
if (Test-Path "$GREPO\.git") { OK "deja clone" }
else { git clone https://github.com/garrytan/gbrain.git $GREPO 2>&1 | Out-Null; OK "clone" }
Push-Location $GREPO; & $BUN install 2>&1 | Out-Null; Pop-Location; OK "deps gbrain installees"
$CLI = "$GREPO\src\cli.ts"

# --- 3. Vault (jamais ecraser) -----------------------------------------------
Say "Vault ($BRAIN)"
if ((Test-Path $BRAIN) -and (Get-ChildItem $BRAIN -Force -ErrorAction SilentlyContinue)) {
    Warn "vault existe deja -> skeleton NON copie (on garde ton contenu)"
} else {
    New-Item -ItemType Directory -Force -Path $BRAIN | Out-Null
    Copy-Item "$REPO\vault-skeleton\*" $BRAIN -Recurse -Force
    OK "skeleton place"
}

# --- 4. Hooks (copie des .py patches Windows) --------------------------------
Say "Hooks ($HOOKS)"
New-Item -ItemType Directory -Force -Path $HOOKS | Out-Null
Copy-Item "$WIN\hooks\*.py" $HOOKS -Force
Copy-Item "$WIN\gbrain-nightly.ps1" "$HOOKS\gbrain-nightly.ps1" -Force
OK "hooks + gbrain-nightly.ps1 deployes"

# --- 5. Commandes gbq / gbrain (wrappers generes avec chemins detectes) ------
Say "Commandes gbq / gbrain"
New-Item -ItemType Directory -Force -Path $BIN | Out-Null
$wrapper = "@echo off`r`n`"$BUN`" run `"$CLI`" %*`r`n"
[System.IO.File]::WriteAllText("$BIN\gbrain.cmd", $wrapper, $utf8)
[System.IO.File]::WriteAllText("$BIN\gbq.cmd",   $wrapper, $utf8)
$userPath = [Environment]::GetEnvironmentVariable('Path','User')
if (($userPath -split ';') -notcontains $BIN) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $BIN), 'User')
    OK "gbq/gbrain -> $BIN (ajoute au PATH - effectif dans un nouveau terminal)"
} else { OK "gbq/gbrain -> $BIN (deja sur le PATH)" }
$env:Path = "$env:Path;$BIN"

# --- 6. GBrain init (PGLite, sans embedding pour l'instant) ------------------
# NB embedding_dimensions : laisse implicite (defaut gbrain pour zembed-1). On NE
# pin PAS de valeur devinee : init + embed tournent sur la meme version gbrain
# (coherent), et le nightly fait 'apply-migrations' apres chaque self-update.
Say "GBrain init"
if (Test-Path "$GDATA\brain.pglite") { OK "deja initialise" }
else {
    & $BUN run $CLI init --pglite --no-embedding --non-interactive 2>&1 | Out-Null
    OK "brain PGLite cree ($GDATA\brain.pglite)"
}
& $BUN run $CLI config set search.mode balanced 2>&1 | Out-Null

# --- 7. Lanceurs wscript + taches planifiees (fix CTRL_C) --------------------
Say "Taches planifiees (Planificateur, via wscript)"
$vbsNightly = "CreateObject(`"WScript.Shell`").Run `"powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"`"$HOOKS\gbrain-nightly.ps1`"`"`", 0, True`r`n"
$vbsReflect = "CreateObject(`"WScript.Shell`").Run `"`"`"$PYEXE`"`" `"`"$HOOKS\daily-reflection.py`"`"`", 0, True`r`n"
[System.IO.File]::WriteAllText("$HOOKS\run-nightly.vbs",    $vbsNightly, $utf8)
[System.IO.File]::WriteAllText("$HOOKS\run-reflection.vbs", $vbsReflect, $utf8)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$an = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$HOOKS\run-nightly.vbs`""
Register-ScheduledTask -TaskName "brain-gbrain-nightly" -Action $an -Trigger (New-ScheduledTaskTrigger -Daily -At 4:00AM) -Principal $principal -Settings $settings -Force | Out-Null
$ar = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$HOOKS\run-reflection.vbs`""
Register-ScheduledTask -TaskName "brain-reflection" -Action $ar -Trigger @((New-ScheduledTaskTrigger -Daily -At 12:00PM),(New-ScheduledTaskTrigger -Daily -At 11:00PM)) -Principal $principal -Settings $settings -Force | Out-Null
OK "brain-gbrain-nightly (04:00) + brain-reflection (12:00/23:00)"

# --- 8. settings.json (merge hooks, non-destructif ; commande = python complet)
Say "Enregistrement des hooks (~/.claude/settings.json)"
$sp = "$H\.claude\settings.json"
New-Item -ItemType Directory -Force -Path (Split-Path $sp) | Out-Null
$cfg = if (Test-Path $sp) { Get-Content $sp -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
if (-not $cfg) { $cfg = [pscustomobject]@{} }
if (-not $cfg.PSObject.Properties['hooks']) { $cfg | Add-Member -NotePropertyName hooks -NotePropertyValue ([pscustomobject]@{}) -Force }
$hooksFwd = ($HOOKS -replace '\\','/')
$pyFwd    = ($PYEXE -replace '\\','/')
function Ensure-Hook($evt, $names) {
    if (-not $cfg.hooks.PSObject.Properties[$evt]) { $cfg.hooks | Add-Member -NotePropertyName $evt -NotePropertyValue @() -Force }
    $arr = @($cfg.hooks.$evt)
    $grp = $arr | Where-Object { -not $_.matcher } | Select-Object -First 1
    if (-not $grp) { $grp = [pscustomobject]@{ hooks = @() }; $arr = $arr + $grp }
    foreach ($n in $names) {
        $present = @($grp.hooks | Where-Object { $_.command -like "*$n*" }).Count -gt 0
        if (-not $present) {
            $cmd = "`"$pyFwd`" `"$hooksFwd/$n`""
            $grp.hooks = @($grp.hooks) + ([pscustomobject]@{ type='command'; command=$cmd })
        }
    }
    $cfg.hooks.$evt = $arr
}
Ensure-Hook "UserPromptSubmit" @("correction-detector.py")
Ensure-Hook "Stop" @("session-logger.py","session-indexer.py","session-recap.py")
[System.IO.File]::WriteAllText($sp, ($cfg | ConvertTo-Json -Depth 20), $utf8)
OK "hooks enregistres (UserPromptSubmit + Stop, via $PYEXE)"

# --- 9. CLAUDE.md global (append, marqueur, jamais ecraser) ------------------
Say "CLAUDE.md global"
$gc = "$H\.claude\CLAUDE.md"
$existing = if (Test-Path $gc) { [System.IO.File]::ReadAllText($gc) } else { "" }
if ($existing -match "<!-- brain-in-a-box -->") { OK "deja present (skip)" }
else {
    $block = [System.IO.File]::ReadAllText("$WIN\CLAUDE.global.windows.md")
    [System.IO.File]::AppendAllText($gc, "`r`n`r`n" + $block, $utf8)
    OK "bloc ajoute (ton contenu preserve)"
}

# --- 10. git init du vault ---------------------------------------------------
Say "git init vault"
if (Test-Path "$BRAIN\.git") { OK "deja un repo git" }
else {
    [System.IO.File]::WriteAllText("$BRAIN\.gitignore", ".obsidian/`n.DS_Store`nThumbs.db`ndesktop.ini`n", $utf8)
    Push-Location $BRAIN
    git init -q; git add -A
    git -c user.email="brain@local" -c user.name="brain" commit -q -m "brain init"
    Pop-Location
    OK "repo vault cree"
}

# --- 11. Cle ZeroEntropy + embedding -----------------------------------------
Say "ZeroEntropy (embeddings - compte gratuit dashboard.zeroentropy.dev)"
$cfgPath = "$GDATA\config.json"
$gconf = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
if (-not $gconf) { $gconf = [pscustomobject]@{} }
if ($gconf.PSObject.Properties['zeroentropy_api_key'] -and $gconf.zeroentropy_api_key) {
    OK "cle deja configuree"
} else {
    $sec = Read-Host "  Colle ta cle ZeroEntropy (ze_...) ou Entree pour differer" -AsSecureString
    $key = [System.Net.NetworkCredential]::new("", $sec).Password
    if ($key) {
        $gconf | Add-Member -NotePropertyName zeroentropy_api_key -NotePropertyValue $key -Force
        $gconf | Add-Member -NotePropertyName embedding_model -NotePropertyValue "zeroentropyai:zembed-1" -Force
        if ($gconf.PSObject.Properties['embedding_disabled']) { $gconf.PSObject.Properties.Remove('embedding_disabled') }
        [System.IO.File]::WriteAllText($cfgPath, ($gconf | ConvertTo-Json -Depth 20), $utf8)
        # Durcir les ACL (equivalent chmod 600) : la cle est en clair dans ce fichier.
        icacls $cfgPath /inheritance:r /grant:r "$($env:USERNAME):(R,W)" 2>&1 | Out-Null
        OK "cle ecrite (ACL durcies) + embedding active (zembed-1)"
        & $BUN run $CLI import $BRAIN --no-embed 2>&1 | Out-Null
        & $BUN run $CLI embed --stale 2>&1 | Out-Null
        OK "vault importe + embedde"
    } else { Warn "cle differee - lance plus tard : windows\set-ze-key.ps1 puis 'gbq embed --stale'" }
}

# --- 12. Mode equipe (BrainCo) -----------------------------------------------
if ($Company) {
    Say "Mode equipe - BrainCo ($Company)"
    if (Test-Path "$BRAINCO\.git") { OK "deja clone" }
    else { git clone $Company $BRAINCO 2>&1 | Out-Null; OK "clone -> $BRAINCO" }
    $srcList = (& $BUN run $CLI sources list) 2>$null
    if ($srcList -match "(?m)^\s*company\s") { OK "source 'company' deja la" }
    else { & $BUN run $CLI sources add company --path $BRAINCO 2>&1 | Out-Null; OK "source 'company' ajoutee" }
    & $BUN run $CLI sources federate company 2>&1 | Out-Null
    & $BUN run $CLI sync --source company --no-embed 2>&1 | Out-Null
    OK "BrainCo indexe (federe)"
}

# --- 13. Obsidian (optionnel) ------------------------------------------------
Say "Obsidian (pour parcourir le vault)"
if (Test-Path "$env:LOCALAPPDATA\Programs\Obsidian\Obsidian.exe") { OK "deja installe" }
elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install --id Obsidian.Obsidian -e --accept-source-agreements --accept-package-agreements --scope user 2>&1 | Out-Null
    OK "installe via winget"
} else { Warn "winget absent - telecharge Obsidian sur https://obsidian.md" }

# --- 14. Smoke-test des hooks (syntaxe) --------------------------------------
Say "Verification des hooks"
$hookFail = $false
Get-ChildItem "$HOOKS\*.py" | ForEach-Object {
    & $PYEXE -m py_compile $_.FullName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn "hook KO (syntaxe) : $($_.Name)"; $hookFail = $true }
}
if (-not $hookFail) { OK "5 hooks compiles (syntaxe OK)" }

Write-Host "`n[OK] brain-in-a-box installe." -ForegroundColor Green
Write-Host "Prochaine etape - l'ONBOARDING (15 min) :" -ForegroundColor Cyan
Write-Host "  cd $BRAIN ; claude   puis colle : 'Lis onboarding/ONBOARDING.md et lance-le pour me personnaliser.'"
Write-Host "Verifs : gbq query `"test`"  |  Get-ScheduledTask brain-*  |  ouvre Obsidian sur $BRAIN"
