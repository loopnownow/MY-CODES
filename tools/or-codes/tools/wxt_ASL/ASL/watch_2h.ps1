# watch_2h.ps1 - 每 2 小时自动记录一次 ExploreASL 进度快照
# 运行:  powershell -NoProfile -ExecutionPolicy Bypass -File watch_2h.ps1
# 输出:  L:\00used\wxt_ASL\logs\progress_every2h.log
# 终止:  关闭该 powershell 进程，或删除此文件后重启
# 注:    脚本位于 0script\ASL\，故以自身位置推导 $root（2026-10-05 归档后）

$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent   # 0script\ASL -> 项目根
$py   = Join-Path $root '0script\ASL\check_progress.py'
$out  = Join-Path $root 'logs\progress_every2h.log'
$total = 25   # 快照次数（25 × 2h = 50 小时；跑满自动退出）

for ($i = 0; $i -lt $total; $i++) {
    try {
        python $py | Out-File -Append -Encoding utf8 $out
    } catch {
        "[$(Get-Date)] snapshot failed: $_" | Out-File -Append -Encoding utf8 $out
    }
    Start-Sleep -Seconds 7200
}