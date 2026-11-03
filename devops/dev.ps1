# Windows 开发入口：安装依赖 / 前台运行后端
param([string]$Command = "dev")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root ".venv\Scripts\python.exe"

switch ($Command) {
  "env" {
    python -m venv "$Root\.venv"
    & $Py -m pip install --upgrade pip
    $lock = "$Root\services\agent_zero\requirements.txt"
    if (-not (Test-Path $lock)) { $lock = "$Root\services\agent_zero\requirements.in" }
    & $Py -m pip install -r $lock
    Write-Host "[env] 完成"
  }
  "env-dev" {
    & $Py -m pip install -r "$Root\services\agent_zero\requirements-dev.txt"
    Write-Host "[env-dev] 完成"
  }
  "ci" {
    & $Py -m compileall -q "$Root\services" "$Root\desktop" "$Root\tests"
    & $Py -m ruff check "$Root\services" "$Root\desktop" "$Root\tests"
    & $Py -m pytest -q "$Root\tests"
  }
  default {
    if (-not (Test-Path $Py)) { throw "缺少 .venv，先运行: .\devops\dev.ps1 env" }
    $env:PYTHONPATH = "$Root;$Root\services\agent_zero"
    $env:AI_PLATFORM_CONFIG_YAML = "$Root\config\agent_zero\config.yaml"
    & $Py "$Root\services\agent_zero\main-agent-zero.py"
  }
}
