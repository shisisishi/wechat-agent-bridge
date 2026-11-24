# WeChat Agent Bridge

> **让微信聊天记录直连你的本机 AI** — 集 **PC 微信 4.x 本地只读直连与数据统计**、**14 个原生 MCP 工具直连 7 大本机 Agent**、**Ollama 离线小模型三层防噪静默待办巡检**、**曜石翡翠 / 瓷雾液态玻璃 Web 看板** 以及 **物理隔离手机端 HTTPS 实时双向同步** 于一体的 Windows 本地优先工作台。

[🎬 **在线观看 60fps WebGL 宣传动画官网**](https://shisisishi.github.io/wechat-agent-bridge/) · [English README](README.en.md) · [MIT License](LICENSE)

---

## 核心特性

1. **零 DLL 注入 · 纯本机离线直连微信 4.x**
   - 自动探测 Windows PC 微信 4.x 数据目录（`xwechat_files\*\db_storage`）。
   - 仅使用 Windows 只读内存 API（`ReadProcessMemory`）提取本地 SQLCipher 4 密钥并离线解密，不注入微信进程、不上传任何云端服务器。
   - 集成并安全加固开源项目 [`zwl467135974/wechat-cli`](https://github.com/zwl467135974/wechat-cli)（默认强制仅绑定 `127.0.0.1`、补齐微信 4.1+ WCDB Cipher 内存扫描与 `koffi` 结构体修复）。
2. **14 个原生 MCP 工具，一键桥接 7 大本机 AI Agent**
   - 一键写入标准 MCP 配置，支持 **Claude Code CLI**、**Codex CLI / Desktop**、**Antigravity**，同时支持直连/转发到 **OpenClaw**、**Obsidian**、**Ollama** 与自定义 **Webhook**。
   - 让本机 AI Agent 自主调用 `list_sessions`、`search_messages`、`get_stats`、`get_timeline`、`get_keyword_trend`、`get_year_report`、`export_chat` 等 14 个工具完成深度检索与分析。
3. **离线小模型（Ollama）三层防噪静默巡检**
   - 独立守护进程（`bridge/auto_todo_worker.py`）定时拉取增量消息，无需消耗云端 API 额度。
   - **三层防噪提准**：① 本地规则预过滤（剔除广告号、水群表情、接龙刷屏）；② 严格 JSON Schema 约束本地模型（默认 `qwen2.5vl:3b`，亦可选配 OpenAI 兼容 / DeepSeek 后端）精准提炼待办；③ 模糊相似度 + 指纹去重写入本地 SQLite，每轮结束后 `keep_alive: "30s"` 自动卸载显存。
4. **曜石翡翠 / 瓷雾浅色 · 液态玻璃 Web 统计工作台**
   - 内置 `http://127.0.0.1:5200` 中文 Web 分析面板：24H 活跃热力矩阵、14 日消息趋势、好友/群聊排行、词云统计、导出 `TXT / JSON / HTML`，并具备 View Transition 圆形水波纹双主题切换与 60fps 微交互动效。
5. **物理隔离双端口 · 手机端 HTTPS 双向实时同步**
   - 本机 `127.0.0.1:5200` 微信聊天金库**彻底不接入公网**；公网隧道仅指向独立隔离的 `127.0.0.1:5201` 手机待办服务（`/api/messages` 等聊天接口严格返回 `404`）。
   - 支持 Cloudflare Quick Tunnel 自动自愈（电脑休眠断线自动重建新隧道）与 `HttpOnly + Secure` 30 天访问口令 Cookie，手机扫码/点链即可与电脑秒级双向同步待办。
6. **Awwwards 灵感 6 幕 WebGL 交互式宣传动画（`docs/`）**
   - 内置零外部 CDN 依赖的纯 WebGL GLSL 着色器 + 3D 渲染引擎，支持时间轴逐帧擦洗、鼠标滚轮交互、程序化 Web Audio 电影音效与一键录制导出 60fps WebM 宣传视频。

---

## 系统架构

```text
PC WeChat 4.x (SQLCipher 4, Local Disk)
        │  [Read-Only Memory Scan · Zero DLL Injection]
        ▼
Local Decrypted Cache (127.0.0.1:5200 · Air-Gapped Vault)
   ├──► Liquid Glass Web Analytics Dashboard (5200)
   ├──► 14 MCP Tools ──► Claude Code / Codex / Antigravity / OpenClaw / Obsidian
   └──► Offline AI Todo Worker (Ollama Qwen 3B · 3-Layer Filter · 30s VRAM Free)
                │
                ▼  (Todos ONLY · No Chat History)
        Local SQLite Inbox (~/.wechat-agent-bridge/inbox.db)
                │
                ▼  [Isolated Port 127.0.0.1:5201 + Cloudflare HTTPS Auto-Heal Tunnel]
        Mobile PWA Web App (Bi-directional Real-Time Sync · Token Cookie Auth)
```

---

## 环境要求

- **操作系统**：Windows 10 / 11（64 位），已登录 PC 微信 4.x
- **Python**：3.10+（标准库为主，`requirements.txt` 仅含少量轻量依赖）
- **Node.js**：18+（用于编译和运行内置 `third_party/wechat-cli`）
- **可选组件**：
  - [Ollama](https://ollama.com/)（用于 0 费用离线待办自动巡检，推荐拉取 `ollama pull qwen2.5vl:3b` 或任意支持 JSON 输出的 3B~7B 模型）
  - [`cloudflared`](https://github.com/cloudflare/cloudflared/releases)（用于手机端 HTTPS 隔离同步：`winget install Cloudflare.cloudflared`）

---

## 快速开始

### 1. 安装依赖与编译内置 `wechat-cli`

```powershell
git clone https://github.com/shisisishi/wechat-agent-bridge.git
cd wechat-agent-bridge

# 安装 Python 依赖
pip install -r requirements.txt

# 编译内置 wechat-cli（TypeScript -> dist）
cd third_party\wechat-cli
npm install
npm run build
cd ..\..
```

### 2. 环境自检与一键接入本机 AI Agent（MCP）

```powershell
# 检测微信数据目录、Node/Python 与已安装的本机 AI Agent
python run.py --detect

# 一键为 Claude Code / Codex / Antigravity 注册 14 个微信 MCP 工具（自动备份原配置）
python run.py --setup-mcp all
```

### 3. 启动各功能模块（均提供一键 `.bat` 脚本）

| 入口脚本 / 命令 | 功能说明 |
| :--- | :--- |
| `启动_微信统计Web面板.bat` | 启动 `http://127.0.0.1:5200` 曜石翡翠液态玻璃 Web 统计与导出工作台 |
| `配置_Agent_MCP直连.bat` | 一键配置 Claude Code / Codex / Antigravity MCP 直连 |
| `启动_离线AI自动巡检.bat` | 启动每 30 分钟静默巡检进程（Ollama 本地模型自动提炼待办并释放显存） |
| `启动_手机在线同步.bat` | 启动隔离端口 `5201` + Cloudflare HTTPS 隧道，输出手机一键免密链接 |
| `启动_宣传动画预览.bat` | 本地 `http://127.0.0.1:8765/` 播放 6 幕 60fps WebGL 交互式宣传动画 |

### 4. 运行单元测试

```powershell
python -m unittest discover -s tests -v
```

---

## 隐私与安全声明

- **本地闭环原则**：所有聊天记录解密、检索、统计和 Ollama 离线推理均在 `127.0.0.1` 本机完成，项目不包含任何遥测或云端数据上报逻辑。
- **物理隔离手机同步**：`127.0.0.1:5201` 手机端服务仅访问待办清单表，不挂载任何微信数据库或消息接口；未授权访问强制返回 `401`。
- **合规提醒**：本项目仅供个人备份、个人数据统计与本机效率工具研究学习使用，请仅对**本人合法登录的账号数据**在本地使用，严禁用于任何侵犯他人隐私或违反相关法律法规的用途。

---

## 致谢与许可

- 本项目内置并安全增强了 [`zwl467135974/wechat-cli`](https://github.com/zwl467135974/wechat-cli)（MIT License，完整上游许可保留于 `third_party/wechat-cli/LICENSE`）。
- 宣传动画（`docs/`）动效手法致敬 Awwwards 精选作品（*Grids*、*Apekon*、*Cyera AI Guardian*、*Cheese AI*、*Piven Ilya*、*Focus Industries*），详见动画右上角「✦ Awwwards 灵感」面板。
- 本项目基于 [MIT License](LICENSE) 开源。
