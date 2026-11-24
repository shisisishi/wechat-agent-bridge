@echo off
chcp 65001 >nul
cd /d "%~dp0"
set HOST=127.0.0.1
set PORT=5200
python -c "from pathlib import Path; from bridge.wechat_db_client import WeChatCLIService; WeChatCLIService(Path('.')).ensure_env_file()"
if not exist "%~dp0third_party\wechat-cli\dist\index.js" (
  echo [INFO] 首次运行，正在编译 wechat-cli ...
  pushd "%~dp0third_party\wechat-cli"
  call npm install && call npm run build
  popd
)
echo 正在启动本地微信导出与统计 Web 服务 ^(http://127.0.0.1:5200^) ...
start "" "http://127.0.0.1:5200"
cd /d "%~dp0third_party\wechat-cli"
node dist\index.js web
pause
