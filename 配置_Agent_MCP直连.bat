@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   一键将 wechat-cli MCP Server 接入 Claude Code / Codex / Antigravity
echo ============================================================
echo.
python "%~dp0run.py" --setup-mcp all
echo.
echo 完成后重启对应 AI Agent 即可直接查询与统计微信聊天记录。
pause
