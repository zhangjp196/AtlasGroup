# 构建 Windows 桌面端：dist\AtlasX\AtlasX.exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }

# 前端必须先构建，PyInstaller 会把 web/dist 一起收进 exe；避免打包到旧产物。
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "找不到 npm：打包前需要 Node（仅构建期）来构建 frontend/。"
}
Push-Location (Join-Path $Root "frontend")
try {
    if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund }
    npm run build
} finally {
    Pop-Location
}
if (-not (Test-Path (Join-Path $Root "web\dist\index.html"))) {
    throw "前端构建产物缺失：web\dist\index.html"
}

& $Py -m PyInstaller --noconfirm --clean `
    --distpath "$Root\dist" `
    --workpath "$Root\build\desktop" `
    "$Root\desktop\AtlasX-windows.spec"

Write-Host ""
Write-Host "✓ 构建完成：$Root\dist\AtlasX\AtlasX.exe"
Write-Host "  数据目录：%APPDATA%\Atlas（沿用旧名，兼容已有会话）"
