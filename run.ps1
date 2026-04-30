param(
    [string]$Model   = "llama3",
    [string]$AppHost = "127.0.0.1",
    [int]   $Port    = 8000
)

# ── Caminhos ────────────────────────────────────────────────────────────────
$projectDir  = $PSScriptRoot
$uvicornExe  = Join-Path $projectDir ".venv\Scripts\uvicorn.exe"
$pipExe      = Join-Path $projectDir ".venv\Scripts\pip.exe"

Set-Location $projectDir

# ── 1. Instalar / atualizar dependencias ────────────────────────────────────
if (-not (Test-Path $pipExe)) {
    Write-Host "[ERRO] pip nao encontrado em .venv\Scripts\pip.exe" -ForegroundColor Red
    Write-Host "       Crie o venv com: python -m venv .venv" -ForegroundColor Yellow
    exit 1
}

Write-Host "[setup] Instalando dependencias (pip install -r requirements.txt)..." -ForegroundColor Yellow
& $pipExe install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERRO] pip install falhou." -ForegroundColor Red
    exit 1
}

# ── 2. Checar Ollama instalado ───────────────────────────────────────────────
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "[ERRO] Ollama nao encontrado no PATH." -ForegroundColor Red
    Write-Host "       Baixe em: https://ollama.com/download" -ForegroundColor Yellow
    exit 1
}

# ── 3. Subir Ollama se nao estiver rodando ───────────────────────────────────
$ollamaRunning = $false
try {
    $resp = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
    $ollamaRunning = ($resp.StatusCode -eq 200)
} catch {}

$ollamaProcess = $null
if (-not $ollamaRunning) {
    Write-Host "[ollama] Iniciando servidor Ollama em background..." -ForegroundColor Cyan
    $ollamaProcess = Start-Process "ollama" -ArgumentList "serve" -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
} else {
    Write-Host "[ollama] Servidor ja esta rodando." -ForegroundColor Green
}

# ── 4. Garantir que o modelo existe ─────────────────────────────────────────
Write-Host "[ollama] Verificando modelo '$Model'..." -ForegroundColor Cyan
$tags = (Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -ErrorAction SilentlyContinue)
$modeloExiste = ($tags.models | Where-Object { $_.name -like "$Model*" }).Count -gt 0

if (-not $modeloExiste) {
    Write-Host "[ollama] Baixando modelo '$Model' (pode demorar na primeira vez)..." -ForegroundColor Yellow
    ollama pull $Model
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERRO] Falha ao baixar o modelo '$Model'." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[ollama] Modelo '$Model' ja disponivel." -ForegroundColor Green
}

# ── 5. Rodar FastAPI ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  AgroVision AI" -ForegroundColor Green
Write-Host "  http://$AppHost`:$Port" -ForegroundColor Green
Write-Host "  Modelo: $Model" -ForegroundColor Green
Write-Host "  Ctrl+C para encerrar" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""

try {
    & $uvicornExe "app:app" "--host" $AppHost "--port" $Port "--reload"
} finally {
    if ($ollamaProcess -and -not $ollamaProcess.HasExited) {
        Write-Host "[ollama] Encerrando servidor Ollama..." -ForegroundColor Yellow
        Stop-Process -Id $ollamaProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
