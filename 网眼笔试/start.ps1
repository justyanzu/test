# 一键启动：创建 venv（若不存在）、安装依赖、运行 Streamlit
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv")) {
    Write-Host "正在创建虚拟环境..."
    python -m venv venv
}

Write-Host "正在安装依赖..."
& ".\venv\Scripts\pip" install -r requirements.txt -q

Write-Host "正在启动 GitHub 仓库体检..."
& ".\venv\Scripts\streamlit" run app.py
