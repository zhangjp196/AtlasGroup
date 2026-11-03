# 构建 Windows 桌面端：dist\AtlasX\AtlasX.exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

& $Py -m PyInstaller --noconfirm --clean `
    --distpath "$Root\dist" `
    --workpath "$Root\build\desktop" `
    "$Root\desktop\AtlasX-windows.spec"

Write-Host ""
Write-Host "✓ 构建完成：$Root\dist\AtlasX\AtlasX.exe"
Write-Host "  数据目录：%APPDATA%\Atlas（沿用旧名，兼容已有会话）"
