@echo off
chcp 65001 >nul
title NLP-to-Lean4 辅助证明助手

:: 无论从哪里双击，都切换到本脚本所在的 app 目录
cd /d "%~dp0"

echo ============================================================
echo   NLP-to-Lean4: 自然语言辅助证明器（Streamlit 版）
echo ============================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: 检查并安装依赖
echo [1/2] 检查依赖...
pip install -r requirements.txt -q 2>nul
if %errorlevel% neq 0 (
    echo [警告] 依赖安装可能失败，尝试继续...
)

:: 加载 .env
if exist ".env" (
    echo [信息] 检测到 .env 文件
) else (
    echo [信息] 未检测到 .env，可使用演示模式或手动填入 API Key
)

:: 启动 Streamlit
echo [2/2] 启动 Streamlit 服务...
echo.
echo   访问地址: http://localhost:8501
echo   按 Ctrl+C 停止服务
echo ============================================================
echo.

streamlit run app.py

pause
