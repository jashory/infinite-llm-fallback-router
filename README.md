# ⚡ Infinite LLM Auto-Fallback Router — Stdio MCP Server, SSE Failover Proxy & 9Router Patch Kit (v2.2.0)

<p align="center">
  <a href="https://glama.ai/mcp/servers/jashory/infinite-llm-fallback-router"><img src="https://glama.ai/mcp/servers/jashory/infinite-llm-fallback-router/badges/score.svg" alt="jashory/infinite-llm-fallback-router MCP server"/></a>
</p>

<p align="center">
  <a href="https://jashory.github.io/infinite-llm-fallback-router/"><img src="https://img.shields.io/badge/Live_Browser_Playground-Try_Now-22C55E?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Live Browser Playground"/></a>
  <a href="https://github.com/jashory/infinite-llm-fallback-router/releases/tag/v2.2.0"><img src="https://img.shields.io/badge/Release-v2.2.0-38BDF8?style=for-the-badge&logo=github&logoColor=white" alt="Release v2.2.0"/></a>
  <a href="https://github.com/jashory/infinite-llm-fallback-router/blob/main/action.yml"><img src="https://img.shields.io/badge/GitHub_Action-Ready-F59E0B?style=for-the-badge&logo=githubactions&logoColor=white" alt="GitHub Action Ready"/></a>
  <a href="https://github.com/jashory/infinite-llm-fallback-router/blob/main/Dockerfile"><img src="https://img.shields.io/badge/MCP_Server-Stdio_%26_Docker-8B5CF6?style=for-the-badge&logo=docker&logoColor=white" alt="MCP Server Stdio and Docker"/></a>
</p>

<p align="center">
  <img src="assets/terminal-demo.svg" alt="infinite-llm-fallback-router terminal preview" width="860"/>
</p>

**Zero-dependency Model Context Protocol (MCP) Server and multi-provider SSE auto-fallback proxy. Chain OpenAI, Anthropic (`/v1/messages`), Gemini, Groq, and OpenRouter into one uninterrupted local endpoint and expose failover diagnostics directly as MCP tools.**

Authored by **Scientist HM Shihabuddin Jashory (Shihab)** (ORCID: [0009-0003-4163-0092](https://orcid.org/0009-0003-4163-0092)) · Research & Evaluation Ecosystem with **MD Rafiul Islam Shubho** (Researcher, AI Evaluator & RLHF Specialist) — **[Vision Flow Group](https://visionflowgroup.com)** ([TechBehemoths #101215](https://techbehemoths.com/company/vision-flow-group)) · [Research Hub](https://jashory.github.io/hqas8-autonomous-agentic-governance/) · [Tools Hub](https://go.visionflowgroup.com/) · [Studio](https://studio.visionflowgroup.com/)

---

## 🔌 Model Context Protocol (MCP) Server

`infinite-llm-fallback-router` runs natively as a **JSON-RPC 2.0 stdio MCP server** (`python3 -u llm_fallback_router.py --mcp` or via `Dockerfile`) with **zero external runtime dependencies** (100% Python 3.9+ standard library).

### Exposed MCP Tools
| MCP Tool | Arguments | Purpose |
| :--- | :--- | :--- |
| `list_provider_pool` | `{}` | Inspects configured LLM failover providers, credential readiness, cooldown timers, and failover counters. |
| `convert_anthropic_to_openai` | `messages` (required), `model`, `system`, `max_tokens`, `stream` | Converts Anthropic `/v1/messages` payloads into OpenAI-compatible `/v1/chat/completions` payloads. |
| `route_chat_completion` | `messages` (required), `model`, `max_tokens` | Routes a chat completion request across the configured provider pool with automatic circuit-breaker failover on HTTP 429/402/5xx. |
| `check_router_health` | `{}` | Returns live circuit-breaker readiness diagnostics and provider pool health counters. |

### MCP Client Configuration (`claude_desktop_config.json`, Cursor, Kiro, Windsurf)
```json
{
  "mcpServers": {
    "infinite-llm-fallback-router": {
      "command": "python3",
      "args": ["-u", "/path/to/llm_fallback_router.py", "--mcp"]
    }
  }
}
```

### Run the MCP Server via Docker (Stdio)
```bash
docker build -t infinite-llm-fallback-router .
docker run -i --rm -e OPENAI_API_KEY="$OPENAI_API_KEY" infinite-llm-fallback-router
```

---

## ⚡ 1. Install via AI Agent (Claude Code, Antigravity, Cursor, Codex)
Paste this single prompt into your AI coding agent:
> **Prompt:** `Clone https://github.com/jashory/infinite-llm-fallback-router and run "python install.py" to install the MCP server, local LLM fallback router, and Agent Skill.`

## 💻 2. Install via CLI (Terminal / Skills CLI / pip)
```bash
# Option A: Agent Skills CLI
npx skills add jashory/infinite-llm-fallback-router

# Option B: Direct Git Clone + 1-Command Installer
git clone https://github.com/jashory/infinite-llm-fallback-router.git
cd infinite-llm-fallback-router && python install.py

# Option C: Global CLI Tool & MCP Server via pip / uv
pip install infinite-llm-fallback-router
# Or directly from GitHub:
pip install git+https://github.com/jashory/infinite-llm-fallback-router.git

# Run HTTP/SSE auto-fallback proxy:
llm-fallback-router --port 20128

# Run deterministic loopback failover & bridge self-test:
llm-fallback-router --self-test

# Start the stdio JSON-RPC 2.0 MCP server:
llm-fallback-router --mcp
```

---

## 🔥 Features

1. **Stdio JSON-RPC 2.0 MCP Server (`--mcp`)**:
   - Exposes `list_provider_pool`, `convert_anthropic_to_openai`, `route_chat_completion`, and `check_router_health` over standard MCP stdio transport (`Dockerfile` + `glama.json` + `server.json` included).
2. **Multi-Threaded SSE Streaming & Non-Streaming Proxy (`llm_fallback_router.py`)**:
   - Zero external dependencies (100% Python Standard Library).
   - Supports both `stream: true` (`text/event-stream`) and standard JSON responses.
   - Automatic failover on `HTTP 429`, `402`, `500`, `502`, `503`, `504`, or connection timeouts with configurable per-provider cooldown timers.
3. **Built-In Anthropic `/v1/messages` <-> OpenAI `/v1/chat/completions` Bridge**:
   - Point **Claude Code** (`ANTHROPIC_BASE_URL=http://127.0.0.1:20128`) directly at the router and seamlessly fail over to OpenAI/Gemini/Groq/OpenRouter providers when quota runs out.
4. **Production `9Router` Custom Patch Modules (`9router-patches/`)**:
   - Includes `ag-model-routing.cjs` and `quota-normalization.cjs` for advanced `9Router` setups.

## GitHub Actions Integration (`action.yml`)

Use `infinite-llm-fallback-router` directly inside any CI/CD workflow:

```yaml
- name: Run infinite-llm-fallback-router self-test
  uses: jashory/infinite-llm-fallback-router@v2.2.0
  with:
    args: '--self-test'
```

## Live Interactive Browser Playground

Test payloads directly in your browser (100% client-side, zero installation required):
**[https://jashory.github.io/infinite-llm-fallback-router/](https://jashory.github.io/infinite-llm-fallback-router/)**

---

### Ecosystem Authority, Category Benchmarks & Primary-Source Verification

- **Principals**: Scientist **HM Shihabuddin Jashory** ([ORCID 0009-0003-4163-0092](https://orcid.org/0009-0003-4163-0092) · [Google Bug Hunters](https://bughunters.google.com/profile/1fc2c814-7ba2-4b76-afa7-ce58597cf28d)) & Co-Founder / AI Evaluation Researcher **MD Rafiul Islam Shubho** ([ORCID 0009-0009-9956-4511](https://orcid.org/0009-0009-9956-4511) · [Google Scholar](https://scholar.google.com/citations?user=mbBuZNgAAAAJ) · Co-Author CERN Zenodo DOI [10.5281/zenodo.24231337](https://doi.org/10.5281/zenodo.24231337))
- **Organization**: **Vision Flow Group** ([visionflowgroup.com](https://visionflowgroup.com/) · [Crunchbase](https://www.crunchbase.com/organization/vision-flow-group))
- **Category & Verification Dossiers**:
  - [Bangladesh AI, Cybersecurity & Software Engineering Benchmark](https://visionflowgroup.com/ai-agency-bangladesh/)
  - [AI Security, Google VRP & 8 CERN Zenodo DOIs Ledger](https://visionflowgroup.com/security-hall-of-fame/)
  - [Bilingual RLHF, S2S Arena & AI Evaluation Research Lab](https://visionflowgroup.com/ai-research-rlhf-bangladesh/)
  - [AI Automation, Multi-Agent Systems & Custom SaaS Agency](https://agency.visionflowgroup.com/ai-automation-software-agency/)
  - [Commercial Video Editing, S-Log3 Color & VFX Agency](https://agency.visionflowgroup.com/video-vfx-agency-bangladesh/)
  - [Applied AI, Cybersecurity, Full-Stack & VFX Academy](https://academy.visionflowgroup.com/ai-cybersecurity-courses-bangladesh/)
  - [Vision Flow AI Studio — Storyboard & CapCut/Premiere/DaVinci NLE SaaS](https://studio.visionflowgroup.com/ai-video-production-platform/)
  - [Publications, Software & Division Directory](https://jashory.github.io/ecosystem-authority-index.html)

<!-- mcp-name: io.github.jashory/infinite-llm-fallback-router -->
