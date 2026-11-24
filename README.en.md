# WeChat Agent Bridge

> **Bridge your local WeChat 4.x history directly to your local AI Agents** — a local-first Windows workbench combining **zero-injection WeChat 4.x read-only decryption & analytics**, **14 native MCP tools for 7 local AI Agents**, **offline Ollama 3-layer noise-filtering todo automation**, an **Obsidian Jade / Porcelain Mist Liquid Glass Web Dashboard**, and **physically air-gapped HTTPS mobile todo sync**.

[🎬 **Watch the Live 60fps WebGL Promo Film**](https://shisisishi.github.io/wechat-agent-bridge/) · [中文文档](README.md) · [MIT License](LICENSE)

---

## Key Features

1. **Zero-Injection, 100% Local WeChat 4.x Read-Only Bridge**
   - Automatically detects Windows PC WeChat 4.x storage (`xwechat_files\*\db_storage`).
   - Uses read-only Windows memory inspection (`ReadProcessMemory`) for local SQLCipher 4 decryption — no DLL injection, no cloud upload.
   - Integrates and hardens [`zwl467135974/wechat-cli`](https://github.com/zwl467135974/wechat-cli) (bound strictly to `127.0.0.1`, patched for WeChat 4.1+ WCDB Cipher scanning and `koffi` struct handling).
2. **14 Native MCP Tools × 7 Local AI Agents**
   - One-click MCP configuration installer (with automatic backups) for **Claude Code CLI**, **Codex CLI / Desktop**, and **Antigravity**, plus bridges to **OpenClaw**, **Obsidian**, **Ollama**, and **Webhooks**.
   - Lets your local agents query sessions, search messages, compute timelines/keyword trends/annual reports, and export chats autonomously.
3. **Silent Offline Small-Model Todo Extraction (Ollama)**
   - Background worker (`bridge/auto_todo_worker.py`) periodically scans incremental messages with zero API cost.
   - **3-Layer Precision Pipeline**: deterministic rule pre-filter (drops ads, sticker spam, and repetitive floods) → strict JSON Schema extraction via local Ollama (`qwen2.5vl:3b` by default, or optional OpenAI-compatible backend) → fuzzy similarity & fingerprint deduplication, with `keep_alive: "30s"` automatic VRAM release.
4. **Obsidian Jade / Porcelain Mist Liquid Glass Dashboard**
   - Local `http://127.0.0.1:5200` analytics suite: 24-hour activity heatmap, 14-day message trends, top contacts/groups, word frequency, `TXT / JSON / HTML` export, circular View-Transition theme switching, and 60fps spring micro-interactions.
5. **Air-Gapped Dual-Port Mobile HTTPS Sync**
   - Your WeChat chat vault on `127.0.0.1:5200` is **never exposed to the internet**.
   - Public traffic only hits an isolated todo-only server (`127.0.0.1:5201`, where `/api/messages` returns `404`), protected by an `HttpOnly + Secure` 30-day token cookie and an auto-healing Cloudflare Quick Tunnel that recovers automatically after Windows sleep/wake.
6. **Awwwards-Inspired 6-Act WebGL Interactive Promo Site (`docs/`)**
   - Zero-CDN custom WebGL GLSL aurora shader, 3D triangulated orb punch-through transition, 3D MCP constellation, FLIP UI morph, curved liquid glass carousel, procedural Web Audio soundscape, and 1-click 60fps WebM video export.

---

## Prerequisites

- **OS**: Windows 10 / 11 (64-bit) with PC WeChat 4.x signed in
- **Python**: 3.10+
- **Node.js**: 18+ (for building/running `third_party/wechat-cli`)
- **Optional**:
  - [Ollama](https://ollama.com/) (`ollama pull qwen2.5vl:3b`) for free offline todo extraction
  - [`cloudflared`](https://github.com/cloudflare/cloudflared/releases) (`winget install Cloudflare.cloudflared`) for isolated HTTPS mobile todo sync

---

## Quick Start

```powershell
git clone https://github.com/shisisishi/wechat-agent-bridge.git
cd wechat-agent-bridge

# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Build bundled wechat-cli
cd third_party\wechat-cli
npm install
npm run build
cd ..\..

# 3. Detect local WeChat environment & available AI Agents
python run.py --detect

# 4. Register 14 WeChat MCP tools for Claude Code / Codex / Antigravity
python run.py --setup-mcp all

# 5. Run unit & integration tests
python -m unittest discover -s tests -v
```

### One-Click Windows Launchers
- `启动_微信统计Web面板.bat` — Launch the local Liquid Glass Web Analytics Dashboard (`http://127.0.0.1:5200`)
- `配置_Agent_MCP直连.bat` — One-click MCP setup for local AI Agents
- `启动_离线AI自动巡检.bat` — Start the silent 30-minute offline Ollama todo extraction loop
- `启动_手机在线同步.bat` — Start the isolated `5201` mobile todo server + auto-healing Cloudflare HTTPS tunnel
- `启动_宣传动画预览.bat` — Preview the 6-Act 60fps WebGL promotional film locally (`http://127.0.0.1:8765/`)

---

## Privacy, Security & Compliance

- **Local-First Architecture**: All database decryption, search, analytics, and Ollama inference stay strictly on `127.0.0.1`. No telemetry or cloud uploads exist.
- **Physical Air-Gap for Mobile Sync**: Port `5201` serves only the local todo SQLite table and enforces token-cookie authentication (`401` on unauthorized access, `404` on any chat endpoints).
- **Intended Use**: Designed solely for personal data backup, personal analytics, and local productivity automation on your own account. Please comply with all applicable laws and platform terms.

---

## Credits & License

- Built on top of and hardened from [`zwl467135974/wechat-cli`](https://github.com/zwl467135974/wechat-cli) (MIT License preserved in `third_party/wechat-cli/LICENSE`).
- Motion choreography in `docs/` draws inspiration from curated Awwwards showcases (*Grids*, *Apekon*, *Cyera AI Guardian*, *Cheese AI*, *Piven Ilya*, and *Focus Industries*).
- Released under the [MIT License](LICENSE).
