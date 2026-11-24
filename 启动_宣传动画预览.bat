@echo off
chcp 65001 >nul
title WeChat Agent Bridge - 宣传动画预览
cd /d "%~dp0docs"
where python >nul 2>nul
if %errorlevel%==0 (
  echo 正在启动本地 60fps 宣传动画服务 ^(http://127.0.0.1:8765^)...
  start "" http://127.0.0.1:8765/
  python -m http.server 8765 --bind 127.0.0.1
) else (
  start "" "%~dp0docs\index.html"
)
