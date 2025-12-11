@echo off
chcp 65001 >nul
title Gemini Desktop API Server
color 0A

echo ==========================================
echo       Gemini Desktop API Server
echo ==========================================
echo.

:: 检查Python
py --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

:: 检查并安装依赖
echo [信息] 检查依赖...
py -m pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [信息] 正在安装依赖包...
    py -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

:: 确保安装 requests（调试功能需要）
py -m pip show requests >nul 2>&1
if errorlevel 1 (
    echo [信息] 安装 requests 模块（调试功能需要）...
    py -m pip install requests
    if errorlevel 1 (
        echo [警告] requests 安装失败，调试功能可能不可用
    )
)

:: 检查配置文件
if not exist ".env" (
    echo [警告] 未找到.env配置文件，将从.env.example复制
    copy ".env.example" ".env" >nul 2>&1
    echo [提示] 请编辑.env文件并填入你的Cookie信息
)

echo.
echo [信息] 正在启动服务器...
echo [提示] 服务器启动后会自动打开浏览器
echo [提示] 按 Ctrl+C 停止服务器
echo.

:: 启动服务器
py main.py

if errorlevel 1 (
    echo.
    echo [错误] 服务器启动失败
    pause
)