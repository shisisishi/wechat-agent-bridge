@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动微信待办「本地离线 AI 自动巡检」(Ollama 本地模型，每30分钟静默巡检)...
echo 提示：跑完后 30 秒自动释放显存，0 费用、数据不出本机。
python -m bridge.auto_todo_worker --interval-minutes 30
pause
