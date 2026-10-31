# Ежедневный выпуск прогноза и рассылка (задачи J3–J8).
# Файл сохранён в UTF-8 с BOM: иначе Windows PowerShell 5.1 читает его в кодировке ANSI.
#
# Регистрация в Планировщике заданий (PowerShell от имени пользователя, путь с пробелами допустим):
#   $root = "C:\путь\к\проекту"
#   $act  = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
#           -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\scripts\daily.ps1`""
#   $trg  = New-ScheduledTaskTrigger -Daily -At 05:30
#   $set  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
#   Register-ScheduledTask -TaskName "AralShield daily" -Action $act -Trigger $trg -Settings $set
#
# Ключи:
#   -NotifyOnly   только рассылка (без выпуска прогноза), например вечерний запуск;
#   -Hour 18      передать `notify --hour 18` (рассылка подписчикам с этим часом отправки).
# Скрипт останавливается при первой ошибке Python и возвращает её код,
# поэтому «Последний результат» в Планировщике показывает сбой выпуска, а не код рассылки.
param(
  [switch]$NotifyOnly,
  [int]$Hour = -1
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $root ".venv\Scripts\python.exe"

$logDir = Join-Path $root "reports\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmm"))
Start-Transcript -Path $log -Append | Out-Null

function Invoke-Step([string[]]$cliArgs) {
  Write-Host ("> python -m aralshield.cli " + ($cliArgs -join " "))
  & $py -m aralshield.cli @cliArgs
  if ($LASTEXITCODE -ne 0) {
    Write-Host ("Шаг завершился с ошибкой, код {0}. Дальнейшие шаги не выполняются." -f $LASTEXITCODE)
    Stop-Transcript | Out-Null
    exit $LASTEXITCODE
  }
}

if (-not $NotifyOnly) {
  Invoke-Step @("daily")
}
if ($Hour -ge 0) {
  Invoke-Step @("notify", "--hour", "$Hour")
} else {
  Invoke-Step @("notify")
}
Stop-Transcript | Out-Null
exit 0
