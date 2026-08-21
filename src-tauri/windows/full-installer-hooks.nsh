!macro NSIS_HOOK_POSTINSTALL
  DetailPrint "正在校验并安装 DeepSeek Harness Runtime..."
  ClearErrors
  ExecWait '"$INSTDIR\${MAINBINARYNAME}.exe" --install-bundled-runtime' $0
  ${If} ${Errors}
    StrCpy $0 31
  ${EndIf}
  ${If} $0 <> 0
    SetErrorLevel $0
    ${IfNot} ${Silent}
      MessageBox MB_ICONSTOP "DeepSeek Harness Runtime 安装未完成（错误码：$0）。请关闭正在运行的 DeepSeek Harness 后重试；若仍失败，请重启 Windows 再安装。"
    ${EndIf}
    Abort
  ${EndIf}
  DetailPrint "DeepSeek Harness Runtime 已准备完成。"
!macroend
