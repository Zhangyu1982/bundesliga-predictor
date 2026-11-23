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
$fail = 0
foreach ($f in $Files) {
  $url = "https://raw.githubusercontent.com/$Owner/$Name/$Branch/$f"
  $dest = Join-Path $Repo ($f -replace '/', '\')
  New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
  $tmp = "$dest.tmp"
  $ok = $false
  for ($i = 1; $i -le 4 -and -not $ok; $i++) {
    try {
      $head = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 20
      $expect = [int64]$head.Headers['Content-Length']
      curl.exe -sS -m 30 -o $tmp $url
      $got = (Get-Item $tmp).Length
      if ($got -gt 0 -and $got -eq $expect) { Move-Item -Force $tmp $dest; $ok = $true }
      else { "  retry {0}: size {1} != expected {2}" -f $i, $got, $expect }
    }
    catch { "  retry {0}: {1}" -f $i, $_.Exception.Message; Start-Sleep 2 }
  }
  if (-not $ok) { $fail++ }
  "{0,-42} {1}" -f $f, $(if ($ok) { "ok ($((Get-Item $dest).Length) bytes)" } else { "FAIL" })
}
if ($fail -gt 0) { throw "$fail 个文件校验失败，未落盘" }
"全部同步完成并已校验大小"
