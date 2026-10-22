; What the installer does besides Tauri's template (bundle > windows > nsis > installerHooks).
;
; Uninstalling the app leaves the brain alone (the notes, the kit, Claude Code). Its scheduled tasks,
; though, would go on starting the kit's scripts at night, at noon and in the evening, the app gone:
; the uninstaller offers to remove them, when they are there. Never in an update (/UPDATE, which
; is also how the installer uninstalls an older Bibo) nor without a window (/P; with /S the answer
; is "no").

!macro BIBO_TASK_EXISTS NAME
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Query /TN "${NAME}"'
  Pop $1
  ${If} $1 == "0"
    StrCpy $0 1
  ${EndIf}
!macroend

!macro BIBO_TASK_DELETE NAME
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "${NAME}" /F'
  Pop $1
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
  ${AndIf} $PassiveMode <> 1
    StrCpy $0 0
    !insertmacro BIBO_TASK_EXISTS "BrainNightly"
    !insertmacro BIBO_TASK_EXISTS "BrainReflection"
    !insertmacro BIBO_TASK_EXISTS "BrainSearchReindex"
    ${If} $0 = 1
      ; No by default: someone who uninstalls to reinstall would lose the tasks for good (the app
      ; finds its brain installed and does not schedule them again).
      MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "$(biboRemoveTasks)" /SD IDNO IDNO bibo_tasks_kept
      !insertmacro BIBO_TASK_DELETE "BrainNightly"
      !insertmacro BIBO_TASK_DELETE "BrainReflection"
      !insertmacro BIBO_TASK_DELETE "BrainSearchReindex"
      StrCpy $0 0
      !insertmacro BIBO_TASK_EXISTS "BrainNightly"
      !insertmacro BIBO_TASK_EXISTS "BrainReflection"
      ${If} $0 = 1
        MessageBox MB_OK|MB_ICONEXCLAMATION "$(biboTasksLeft)" /SD IDOK
      ${EndIf}
      bibo_tasks_kept:
    ${EndIf}
  ${EndIf}
!macroend
