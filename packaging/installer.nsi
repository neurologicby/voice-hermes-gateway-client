Unicode True
SetCompressor /SOLID lzma
RequestExecutionLevel user

!include "MUI2.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "0.1.0"
!endif
!ifndef BUILD_DIR
  !define BUILD_DIR "..\dist\VoiceGatewayClient"
!endif
!ifndef OUTPUT_DIR
  !define OUTPUT_DIR "..\dist"
!endif

!define APP_NAME "VoiceGateway Client"
!define APP_EXE "VoiceGatewayClient.exe"
!define APP_PUBLISHER "VoiceGateway contributors"
!define APP_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceGatewayClient"

Name "${APP_NAME}"
OutFile "${OUTPUT_DIR}\VoiceGatewayClient-${APP_VERSION}-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\VoiceGateway Client"
InstallDirRegKey HKCU "Software\VoiceGateway\VoiceClient" "InstallDir"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Запустить VoiceGateway Client"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"

Section "VoiceGateway Client" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "${BUILD_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\VoiceGateway\VoiceClient" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${APP_UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${APP_UNINSTALL_KEY}" "NoRepair" 1
  CreateDirectory "$SMPROGRAMS\VoiceGateway Client"
  CreateShortcut "$SMPROGRAMS\VoiceGateway Client\VoiceGateway Client.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\VoiceGateway Client\Удалить VoiceGateway Client.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Desktop shortcut" SEC_DESKTOP
  CreateShortcut "$DESKTOP\VoiceGateway Client.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\VoiceGateway Client.lnk"
  Delete "$SMPROGRAMS\VoiceGateway Client\VoiceGateway Client.lnk"
  Delete "$SMPROGRAMS\VoiceGateway Client\Удалить VoiceGateway Client.lnk"
  RMDir "$SMPROGRAMS\VoiceGateway Client"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${APP_UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\VoiceGateway\VoiceClient"
SectionEnd
