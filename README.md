# token-panel：AI 用量面板

一個 Docker 容器（FastAPI），顯示 **Claude 5 小時／每週** 與 **ChatGPT (Codex) 5 小時／每週** 的使用率與重置倒數，以 PWA 呈現（iPhone／Mac／Windows 都能安裝）。

- 背景每 5 分鐘抓一次用量，快取在記憶體；`GET /api/usage` 回傳整理後的 JSON
- `POST /api/refresh` 立即重抓（30 秒內只會真的抓一次）
- 任一家失敗不影響另一家；失敗時保留上次成功的數字，並標示 `stale`、`error`、`last_success`
- 憑證放在 volume `/data`（`./data` 目錄），token 自動刷新並寫回檔案（refresh token 會輪替）

> ⚠️ 用的是兩家**未公開**的端點，隨時可能變動。面板本身**沒有登入保護**，只能在區網或 Tailscale 內使用，**不要**在路由器上做 port forwarding。

## 專案結構

```
app/main.py      FastAPI、背景輪詢、快取、/api/usage
app/claude.py    Claude 用量 + OAuth 刷新
app/codex.py     ChatGPT/Codex 用量 + OAuth 刷新（用 limit_window_seconds 判斷 5h/週）
app/static/      PWA（index.html、app.js、style.css、sw.js、manifest、icons）
tests/           假資料測試（解析、刷新／輪替、寫回、錯誤隔離）
Dockerfile, docker-compose.yml, deploy.sh
```

## `/api/usage` 格式

```json
{
  "server_time": "2026-11-10T10:20:45Z",
  "poll_seconds": 300,
  "providers": {
    "claude": {
      "ok": true, "stale": false, "error": null,
      "last_success": "2026-11-10T10:20:41Z", "last_attempt": "2026-11-10T10:20:41Z",
      "plan": "max",
      "windows": {
        "five_hour": {"used_percent": 37.0, "resets_at": "2026-11-10T15:00:00Z"},
        "weekly":    {"used_percent": 82.5, "resets_at": "2026-11-14T03:00:00Z"}
      }
    },
    "codex": { "...": "同上，plan 為 plan_type（plus / pro …）" }
  }
}
```

## 本機測試

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 部署到 NAS

### 1. 複製並啟動

```bash
./deploy.sh <帳號>@<NAS IP>
```

等同於：`scp` 檔案到 NAS 的 `~/token-panel`，然後 `docker compose up -d --build`。
（Synology 上 docker 指令可能需要 `sudo`；舊版則是 `docker-compose`。若需要 sudo，可以 SSH 進去手動執行 `cd ~/token-panel && sudo docker compose up -d --build`。）

### 2. 在容器內登入（只需一次）

容器已設定 `HOME=/data/home`、`CLAUDE_CONFIG_DIR=/data/claude`、`CODEX_HOME=/data/codex`，憑證會寫進 `./data`。

**Claude：**
```bash
ssh <帳號>@<NAS IP>
docker exec -it token-panel claude
```
第一次會先問主題等設定，接著選「Claude account with subscription」（或進入後輸入 `/login`），
把顯示的網址貼到瀏覽器授權，再把瀏覽器給的 code 貼回終端機。成功後輸入 `/exit` 離開。
確認：`docker exec token-panel ls -l /data/claude/.credentials.json`

> 請用 `/login`，不要用 `claude setup-token`：那種長效 token 沒有讀取用量所需的權限。

**ChatGPT / Codex：**
```bash
docker exec -it token-panel codex login --device-auth
```
打開顯示的網址、輸入一次性代碼。若出現 device code 未啟用，先到 ChatGPT「設定 → 安全性」開啟裝置代碼登入。
確認：`docker exec token-panel ls -l /data/codex/auth.json`

### 3. 驗證

```bash
curl -X POST http://<NAS IP>:8787/api/refresh   # 不想等 5 分鐘就立即抓
curl http://<NAS IP>:8787/api/usage
docker logs --tail 50 token-panel
```
兩家都 `"ok": true`、`used_percent` 有數字就成功了。

### 4. Tailscale HTTPS（iPhone 安裝 PWA、外出也能看）

iOS 要求 HTTPS 才能完整使用 PWA（service worker）。最簡單是用 Tailscale Serve：

1. NAS 安裝 Tailscale（Synology／QNAP 套件中心都有，或 `curl -fsSL https://tailscale.com/install.sh | sh`），登入你的帳號。
2. 到 Tailscale 管理後台 → **DNS**：啟用 **MagicDNS** 與 **HTTPS Certificates**。
3. 在 NAS 上執行（Synology 需要 `sudo`）：
   ```bash
   tailscale serve --bg 8787
   tailscale serve status    # 會顯示 https://<nas名稱>.<tailnet>.ts.net
   ```
   憑證由 Tailscale 自動申請與續期，設定會在重開機後保留。
4. iPhone 安裝 Tailscale App 並登入同一帳號 → Safari 打開 `https://<nas名稱>.<tailnet>.ts.net` →
   分享 → **加入主畫面**。
   Mac／Windows：Chrome／Edge 打開同一網址 → 網址列的「安裝」圖示（Mac Safari：檔案 → 加入 Dock）。

只要手機開著 Tailscale，在外面也能看；`ts.net` 網址只有你 tailnet 內的裝置連得到。

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `POLL_SECONDS` | `300` | 背景抓取間隔 |
| `STALE_AFTER_SECONDS` | `POLL_SECONDS*3` | 超過多久沒成功就視為過期 |
| `TZ` | `Asia/Taipei` | 容器時區（只影響 log） |

## 更新

```bash
./deploy.sh <帳號>@<NAS IP>          # 重新部署程式，./data 不會被動到
# 更新 CLI 版本：在 NAS 上 docker compose build --no-cache && docker compose up -d
```
