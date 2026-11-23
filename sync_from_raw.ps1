# 唯一同步入口：不要在别处直接 Invoke-WebRequest / curl raw。
# 实际逻辑在 scripts/sync_from_raw.ps1（带 非空 校验 + 重试 + 大小比对），这里只做统一入口。
param(
  [Parameter(Mandatory = $true)][string]$Repo,
  [Parameter(Mandatory = $true)][string[]]$Files,
  [string]$Branch = "main"
)
$inner = Join-Path $PSScriptRoot "scripts\sync_from_raw.ps1"
if (-not (Test-Path $inner)) { throw "缺少同步实现：$inner" }
& pwsh -NoProfile -File $inner -Repo $Repo -Files $Files -Branch $Branch
exit $LASTEXITCODE
