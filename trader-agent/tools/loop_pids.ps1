# Print the PIDs of running trader-loop processes, one per line.
#
# Used as the single-instance guard of loop_start_bg.bat / loop_stop.bat:
# the 08:50 scheduled task can fire while a loop started by hand is still
# running, and two loops double-manage the same book (duplicate exits/entries).
# No output = no loop running.
#
# Kept as a .ps1 file on purpose: a `powershell -Command "... | ..."` pipeline
# inside a batch `for /f "usebackq"` backquote block hands the pipe to
# PowerShell as a literal '^|' and the command fails silently (measured
# 2026-09-28), so the pipeline lives here instead.
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'run_market_loop' } |
  Select-Object -ExpandProperty ProcessId
