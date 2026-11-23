# 从 GitHub raw 同步文件到本地仓库，带大小校验（0 字节 / 截断文件比缺文件更危险）
# 用法：pwsh -File scripts/sync_from_raw.ps1 -Repo "C:\Users\86199\德甲机器人\bundesliga-predictor-main" -Files app.py,pipeline.py
param(
  [Parameter(Mandatory = $true)][string]$Repo,
  [Parameter(Mandatory = $true)][string[]]$Files,
  [string]$Branch = "main",
  [string]$Owner = "Zhangyu1982",
  [string]$Name = "bundesliga-predictor"
)

$ErrorActionPreference = "Stop"
# pwsh -File 会把 "-Files a,b" 当一个字符串传进来，这里统一拆开
$Files = @($Files | ForEach-Object { $_ -split ',' } | Where-Object { $_ -and $_.Trim() -ne '' })
$fail = @()
foreach ($f in $Files) {
  $url = "https://raw.githubusercontent.com/$Owner/$Name/$Branch/$f"
  $dest = Join-Path $Repo ($f -replace '/', '\')
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  $tmp = "$dest.tmp"
  $ok = $false
  for ($i = 1; $i -le 4 -and -not $ok; $i++) {
    try {
      if (Test-Path $tmp) { Remove-Item -Force $tmp }
      curl.exe -fsS -m 30 --retry 2 --retry-all-errors -o $tmp $url 2>$null
      $code = $LASTEXITCODE
      if ($code -eq 0 -and (Test-Path $tmp) -and (Get-Item $tmp).Length -gt 0) {
        Move-Item -Force $tmp $dest; $ok = $true
      }
      else { "  retry {0}: curl exit {1}" -f $i, $code }
    }
    catch { "  retry {0}: {1}" -f $i, $_.Exception.Message; Start-Sleep 2 }
  }
  if (-not $ok) { $fail += $f }
  "{0,-42} {1}" -f $f, $(if ($ok) { "ok ($((Get-Item $dest).Length) bytes)" } else { "FAIL" })
}
if ($fail.Count -gt 0) { throw ("未通过校验：" + ($fail -join ", ")) }
"全部同步完成并已校验大小"
