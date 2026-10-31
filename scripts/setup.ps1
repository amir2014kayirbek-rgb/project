# Первичная установка: окружение Python, зависимости, сборка веб-панели.
# Файл сохранён в UTF-8 с BOM: иначе Windows PowerShell 5.1 читает его в кодировке ANSI.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-Ok([string]$step) {
  if ($LASTEXITCODE -ne 0) { Write-Host "Ошибка на шаге: $step (код $LASTEXITCODE)"; exit $LASTEXITCODE }
}

if (-not (Test-Path ".venv")) { python -m venv .venv; Assert-Ok "python -m venv" }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
Assert-Ok "pip install --upgrade pip"
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
Assert-Ok "pip install -r requirements.txt"
Push-Location frontend
try {
  npm install
  Assert-Ok "npm install"
  npm run build
  Assert-Ok "npm run build"
} finally {
  Pop-Location
}
Write-Host "Готово. Дальше: .venv\Scripts\python.exe -m aralshield.cli all  (полный конвейер, ~30-40 мин)"
