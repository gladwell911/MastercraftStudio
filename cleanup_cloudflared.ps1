# 清理 cloudflared 相关文件的脚本
# 此脚本将删除不再需要的 cloudflared 文件

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "清理 cloudflared 相关文件" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 检查是否存在 cloudflared.exe
if (Test-Path "cloudflared.exe") {
    Write-Host "正在删除 cloudflared.exe..." -ForegroundColor Yellow
    Remove-Item -Path "cloudflared.exe" -Force
    Write-Host "[✓] cloudflared.exe 已删除" -ForegroundColor Green
} else {
    Write-Host "[✓] cloudflared.exe 不存在，跳过" -ForegroundColor Gray
}

Write-Host ""
$choice = Read-Host "是否删除 .cloudflared 目录？(Y/N)"

if ($choice -eq "Y" -or $choice -eq "y") {
    if (Test-Path ".cloudflared") {
        Write-Host "正在删除 .cloudflared 目录..." -ForegroundColor Yellow
        Remove-Item -Path ".cloudflared" -Recurse -Force
        Write-Host "[✓] .cloudflared 目录已删除" -ForegroundColor Green
    } else {
        Write-Host "[✓] .cloudflared 目录不存在，跳过" -ForegroundColor Gray
    }
} else {
    Write-Host "[✓] 保留 .cloudflared 目录" -ForegroundColor Gray
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "清理完成！" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "后续步骤：" -ForegroundColor Yellow
Write-Host "1. 设置环境变量："
Write-Host "   - REMOTE_CONTROL_TOKEN=your_secret_token"
Write-Host "   - REMOTE_CONTROL_DOMAIN=rc.tingyou.cc"
Write-Host ""
Write-Host "2. 重启程序"
Write-Host ""
Write-Host "详见 DOMAIN_CONNECTION_SETUP.md 文件" -ForegroundColor Cyan
Write-Host ""
