@echo off
REM 清理 cloudflared 相关文件的脚本
REM 此脚本将删除不再需要的 cloudflared 文件

echo.
echo ========================================
echo 清理 cloudflared 相关文件
echo ========================================
echo.

REM 检查是否存在 cloudflared.exe
if exist "cloudflared.exe" (
    echo 正在删除 cloudflared.exe...
    del /f /q "cloudflared.exe"
    echo [✓] cloudflared.exe 已删除
) else (
    echo [✓] cloudflared.exe 不存在，跳过
)

echo.
echo 是否删除 .cloudflared 目录？(Y/N)
set /p choice=
if /i "%choice%"=="Y" (
    if exist ".cloudflared" (
        echo 正在删除 .cloudflared 目录...
        rmdir /s /q ".cloudflared"
        echo [✓] .cloudflared 目录已删除
    ) else (
        echo [✓] .cloudflared 目录不存在，跳过
    )
) else (
    echo [✓] 保留 .cloudflared 目录
)

echo.
echo ========================================
echo 清理完成！
echo ========================================
echo.
echo 后续步骤：
echo 1. 设置环境变量：
echo    - REMOTE_CONTROL_TOKEN=your_secret_token
echo    - REMOTE_CONTROL_DOMAIN=rc.tingyou.cc
echo.
echo 2. 重启程序
echo.
echo 详见 DOMAIN_CONNECTION_SETUP.md 文件
echo.
pause
