@echo off
rem ============================================================
rem  掼蛋 Guandan — 一键停止 Web 服务
rem  只结束命令行里含 guandan.ui.web.app 的进程，
rem  不会误伤恰好占用同一端口的其他程序。
rem ============================================================
setlocal

powershell -NoProfile -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'guandan[.]ui[.]web[.]app' };" ^
  "if (-not $procs) { Write-Host '没有正在运行的掼蛋服务。' }" ^
  "else { foreach ($p in $procs) { Write-Host ('停止 PID ' + $p.ProcessId + ' ...'); Stop-Process -Id $p.ProcessId -Force }; Write-Host '已停止。' }"

pause
endlocal
