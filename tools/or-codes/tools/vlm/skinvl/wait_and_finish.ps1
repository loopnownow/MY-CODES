# wait_and_finish.ps1
# 等待 SkinVL-MM 权重下载完成，然后自动补齐 CLIP、跑环境自检与单图冒烟。
# 用法（在 PowerShell 中；脚本已移 _Codes，venv 需按 mlh_SIX\ref\env-skinvl.md 重建）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File {or_codes}\tools\vlm\skinvl\wait_and_finish.ps1

$ErrorActionPreference = "Stop"
$scriptDir = $PSScriptRoot
$py = "{lab_root}\pre-submit\mlh_SIX\.venv\Scripts\python.exe"
$modelRoot = "E:\_models"
$logDir = "{lab_root}\pre-submit\mlh_SIX\logs"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "finish_$stamp.log"

$env:KMP_DUPLICATE_LIB_OK = "TRUE"
$env:SKINVL_ALLOW_DOWNLOAD = "1"
$env:SKINVL_HF_MIRROR = "https://hf-mirror.com"

function Write-Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

$shards = @(
    @{ Path = Join-Path $modelRoot "SkinVL-MM\model-00001-of-00003.safetensors"; Min = 4900000000 },
    @{ Path = Join-Path $modelRoot "SkinVL-MM\model-00002-of-00003.safetensors"; Min = 4900000000 },
    @{ Path = Join-Path $modelRoot "SkinVL-MM\model-00003-of-00003.safetensors"; Min = 4600000000 }
)

Write-Log "等待 3 个权重分片下载完成（每 60 秒检查一次）"
while ($true) {
    $pending = @()
    foreach ($s in $shards) {
        if (-not (Test-Path $s.Path)) {
            $pending += ("{0}: 缺失" -f (Split-Path $s.Path -Leaf))
            continue
        }
        $len = (Get-Item $s.Path).Length
        if ($len -lt $s.Min) {
            $pending += ("{0}: {1:N1} GB" -f (Split-Path $s.Path -Leaf), ($len / 1GB))
        }
    }
    if ($pending.Count -eq 0) { break }
    Write-Log ("未完成 -> " + ($pending -join "; "))
    Start-Sleep -Seconds 60
}

Write-Log "三个分片齐全，开始补齐 CLIP 视觉塔"
& $py (Join-Path $scriptDir "download_files.py") --task clip --workers 4
Write-Log ("download clip exit code = " + $LASTEXITCODE)

Write-Log "运行环境自检"
& $py (Join-Path $scriptDir "run_env_check.py")
Write-Log ("env check exit code = " + $LASTEXITCODE)

Write-Log "运行单图冒烟"
& $py (Join-Path $scriptDir "smoke_test.py")
Write-Log ("smoke exit code = " + $LASTEXITCODE)

Write-Log "完成。日志：$logFile"
Write-Log "下一步可跑批量：python {or_codes}\tools\vlm\skinvl\infer_batch.py --prompt-mode caption --limit 20"
