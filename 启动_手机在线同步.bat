@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动微信实时待办手机在线同步服务（仅同步待办，物理隔离隐私聊天记录）...
python -m bridge.start_mobile_tunnel
pause
