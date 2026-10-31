# Запуск API и веб-панели: http://127.0.0.1:8000
# Файл сохранён в UTF-8 с BOM: иначе Windows PowerShell 5.1 читает его в кодировке ANSI.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
& "$root\.venv\Scripts\python.exe" -m aralshield.cli serve
exit $LASTEXITCODE
