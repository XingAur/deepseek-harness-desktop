function Find-E2EFileHashCommand {
  Get-Command -Name Get-FileHash -CommandType Cmdlet -ErrorAction SilentlyContinue
}

function Get-E2ESha256 {
  param([Parameter(Mandatory = $true)][string]$LiteralPath)

  if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
    throw "SHA256 target is not a regular file: $LiteralPath"
  }
  $fileHash = Find-E2EFileHashCommand
  if ($null -ne $fileHash) {
    $result = & $fileHash -LiteralPath $LiteralPath -Algorithm SHA256
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Hash) -or $result.Hash -notmatch '^[0-9a-fA-F]{64}$') {
      throw "Get-FileHash returned an invalid SHA256 value: $LiteralPath"
    }
    return $result.Hash.ToLowerInvariant()
  }

  $stream = [System.IO.File]::Open(
    $LiteralPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
  )
  try {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
      $bytes = $sha256.ComputeHash($stream)
    } finally {
      $sha256.Dispose()
    }
  } finally {
    $stream.Dispose()
  }
  $hex = New-Object System.Text.StringBuilder 64
  foreach ($byte in $bytes) {
    [void]$hex.AppendFormat([System.Globalization.CultureInfo]::InvariantCulture, '{0:x2}', $byte)
  }
  return $hex.ToString()
}
