function Remove-OwnedTreeWithoutFollowingReparsePoints([Parameter(Mandatory = $true)][string]$Root) {
  # Root ownership and the fixed LocalAppData boundary are established by the
  # caller.  The Node helper uses lstat/readdir plus non-recursive rm for
  # junction leaves, avoiding PowerShell's per-file provider overhead.
  $node = (Get-Command node.exe -ErrorAction Stop).Source
  & $node (Join-Path $PSScriptRoot 'owned-tree-cleanup.mjs') '--root' ([System.IO.Path]::GetFullPath($Root))
  if ($LASTEXITCODE -ne 0) { throw "Owned tree cleanup failed with exit code $LASTEXITCODE" }
}
