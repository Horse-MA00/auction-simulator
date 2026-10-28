# HAN2601 Auction Simulator

根據 *A Generic Auction Simulator for Programmed and LLM-Based Agents* proposal 製作的原型。系統以 scripted auctioneer、scripted bidders 和中央 receiver，完整執行一場單件物品的 **first-price sealed-bid auction**，並提供註冊、登入、管理員／一般使用者權限及 PostgreSQL 支援。不需要 LLM API key。

## 可以做甚麼

- 設定物品、底價、bidder 估值和預算。
- 逐步執行或自動播放：開標、各 bidder 提交、關閉投標、結算。
- 由 receiver 檢查訊息來源、session、round、次序及合法性。
- 計算得標者、付款、賣方收入和 bidder surplus。
- 註冊和登入帳戶；管理員建立實驗並將 bidders 指派給使用者。
- 一般使用者只查看獲指派的實驗和自己的私人資料；管理員可查看完整實驗。
- 用 SQLite 或 PostgreSQL 保留帳戶、session 和 event log；重新啟動後可以繼續。
- 透過 WebSocket 更新事件、逐事件重播，並匯出已完成實驗的 JSON。

預設啟動及 Docker port 都綁定 `127.0.0.1`。使用者帳戶與 scripted bidder 是不同角色：帳戶控制資料可見性，投標仍由程式策略執行。沒有預設管理員帳號或密碼。

## 帳戶與權限

| 操作／資料 | 管理員 | 一般使用者 |
| --- | --- | --- |
| 建立實驗、逐步執行、自動執行 | 可以 | 不可以 |
| 實驗列表 | 全部 | 只有指派給自己的實驗 |
| 估值、預算及出價 | 全部，包含進行中的出價 | 只有自己的 bidders |
| 結算、事件、重播及匯出 | 完整資料 | 依相同權限過濾 |
| 建立管理員 | 本機 CLI | 不可以自行升級 |

公開註冊一律建立 `user`，即使它是第一個帳戶。Username 為 3–32 個英文字母、數字、`_` 或 `-`，儲存時轉成小寫；新設密碼為 5–20 字元，前後空白會保留。密碼使用 Argon2id 雜湊，登入使用有效期 12 小時的 HttpOnly、SameSite=Strict cookie；伺服器只儲存 session token 的雜湊。登出會撤銷 session。

## 拍賣規則與策略

**拍賣機制**和**出價策略**是不同的設定。Proposal 中的「first-bid」在這個版本明確拆成以下兩部分：

| 項目 | v0 規則 |
| --- | --- |
| 拍賣機制 | First-price sealed-bid，單件物品 |
| Scripted strategy | `bid = valuation * 4 // 5`，估值的 80%，向下取整 |
| 預算不足 | 若完整的固定出價超過預算，該 bidder 放棄投標 |
| 得標者 | 達到底價的有效出價中，最高出價者 |
| 同價 | 按建立 session 時的 bidder 次序決定 |
| 付款 | 得標者支付自己的出價；其他人支付 0 |
| 沒有合資格出價 | 不成交，所有人支付 0 |
| 效用 | `utility = value of allocated item - payment` |
| 單位 | 整數模擬 credits，不涉及真實金錢 |

估值為 1 時，取整後出價為 0；若底價也是 0，該出價仍有效。

例如估值為 100、預算為 100，固定出價為 80；如果得標，效用為 `100 - 80 = 20`，即估值的 20%。這不是對所有拍賣機制或所有策略的獲利保證。其他 bidders 的私人資料不會出現在使用者的回應中；使用者可立即看到自己已提交的出價，管理員則可以檢查所有出價。

## 本機啟動

需要 Python 3.11 或以上、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及 Node.js 22（22.12 或以上）和 npm。請在 repository 根目錄執行。

先安裝依賴並建立管理員。CLI 會安全地提示輸入和確認密碼：

```bash
uv sync --frozen
uv run python -m backend.auction_sim.admin create admin
```

然後啟動 backend：

```bash
uv run uvicorn backend.auction_sim.main:app --host 127.0.0.1 --port 8000 --reload
```

再開另一個 terminal 啟動 frontend：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

開啟 [http://127.0.0.1:5173](http://127.0.0.1:5173) 並以剛建立的管理員登入。Vite 會將 `/api` 和 WebSocket 請求轉到 port 8000。Backend 的互動 API 文件位於 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

其他使用者可自行註冊，管理員再從建立實驗表單的帳戶選單指派 bidder。沒有被指派 bidder 的使用者會看到空的實驗列表。若需要將已有帳戶提升為管理員，在有權存取 backend 的本機 terminal 執行：

```bash
uv run python -m backend.auction_sim.admin promote USERNAME
```

此操作會撤銷該帳戶的現有 sessions，需要重新登入。建立／提升管理員的 CLI 必須使用與應用程式相同的資料庫設定。

亦可先 build frontend，再由 FastAPI 同時提供 API 和網頁：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv run uvicorn backend.auction_sim.main:app --host 127.0.0.1 --port 8000
```

這時直接開啟 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

## 一個有登入狀態的 API 實驗

先執行 `uv run python` 開啟 Python shell，再貼入以下程式。密碼由 `getpass` 輸入，不會寫進範例或 command history。`httpx.Client` 保留登入 cookie，所有登入後的寫入操作都需要 `X-CSRF-Token`。

```python
import getpass
import httpx

client = httpx.Client(base_url="http://127.0.0.1:8000")
login = client.post("/api/auth/login", json={
    "username": "admin", "password": getpass.getpass("Admin password: ")
})
login.raise_for_status()
client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
created = client.post("/api/runs", json={
    "item_name": "Demo lot", "reserve_price": 50,
    "bidders": [
        {"id": "alice", "name": "Alice", "valuation": 100, "budget": 100},
        {"id": "bob", "name": "Bob", "valuation": 90, "budget": 100}
    ]
})
created.raise_for_status()
result = client.post(f"/api/runs/{created.json()['id']}/execute")
result.raise_for_status()
print(result.json()["results"])
client.post("/api/auth/logout").raise_for_status()
client.close()
```

這個例子由 Alice 以 80 credits 得標，效用為 20；Bob 支付 0。未設定 `owner_user_id` 的 bidders 只供管理員觀察。若想逐步執行，使用 `/advance` 代替 `/execute`。

## 測試

```bash
uv run pytest
npm --prefix frontend test
npm --prefix frontend run build
```

測試涵蓋結算、底價、同價、預算、訊息驗證、重播、持久化、登入／登出、CSRF 及資料存取隔離。GitHub Actions 設定了 SQLite tests、PostgreSQL integration tests、frontend tests 和 frontend production build。PostgreSQL 測試需要 `AUCTION_TEST_DATABASE_URL`；未設定時會跳過，見 [資料庫說明](docs/database.md)。

## Render 雲端部署

Repository 已包含 `render.yaml`，可從 Render 的 **New → Blueprint** 選擇 `auction-simulator-v0` branch，一次建立前後端 Docker service 和 PostgreSQL。此配置為付費入門方案，約 US$13/月起；建立前請確認 Render 顯示的費用。完整步驟及雲端 admin 建立方法見 [Render 部署說明](docs/render.md)。

## Docker

需要啟動 Docker Desktop 或 Docker Engine：

```bash
docker compose up --build
```

開另一個 terminal，在同一個 container 資料庫建立管理員：

```bash
docker compose exec simulator python -m backend.auction_sim.admin create admin
```

然後開啟 [http://127.0.0.1:8000](http://127.0.0.1:8000) 登入。SQLite 保存在 named volume `auction-data`，重建 container 不會清除帳戶或實驗。`docker compose down` 會停止服務並保留 volume。

多人使用建議選擇 PostgreSQL。專案提供獨立的 `compose.postgres.yaml`：

```bash
# 先在未納入 Git 的 .env 設定 POSTGRES_PASSWORD，使用自行產生的 URL-safe 密碼。
docker compose -f compose.postgres.yaml up --build
docker compose -f compose.postgres.yaml exec simulator python -m backend.auction_sim.admin create admin
```

這份設定要求你提供資料庫密碼，沒有內建預設密碼。PostgreSQL 18 的資料 volume 掛在 `/var/lib/postgresql`，且沒有對外發布 database port。兩種 Compose 設定使用不同資料庫；切換設定不會自動搬移 SQLite 資料。

## 設定與目錄

| 環境變數 | 預設／用途 |
| --- | --- |
| `AUCTION_DATABASE_URL` | 可選，例如 `postgresql+psycopg://USER:PASSWORD@HOST/DB`；設定後優先於 SQLite 路徑 |
| `AUCTION_DB_PATH` | `data/auction.sqlite3`，沒有 database URL 時使用 |
| `AUCTION_FRONTEND_DIR` | `frontend/dist`，已 build 的 frontend 位置 |
| `AUCTION_COOKIE_SECURE` | 本機 HTTP 預設 `false`；HTTPS 部署設為 `true` |
| `AUCTION_ALLOWED_ORIGINS` | 逗號分隔的完整 origin；本機預設允許 `localhost`／`127.0.0.1` 的 8000、5173 ports。Render 部署會自動加入 `RENDER_EXTERNAL_URL`，自訂域名在此另外設定 |
| `POSTGRES_PASSWORD` | PostgreSQL Compose 必填；本機直接啟動 backend 不使用此變數 |

`.env.example` 提供參考值。Docker Compose 會讀 `.env` 進行設定替換；直接啟動的 backend 只讀取 process environment，不會自行載入 `.env`。前後端採同一 origin／Vite proxy，不需要開放 CORS。若透過 HTTPS host 部署，需將該完整 origin 加入允許清單並啟用 Secure cookie。

```text
backend/auction_sim/   # API、auth、RBAC、receiver、rules、agents、SQL storage
backend/tests/         # Backend tests
frontend/              # React + TypeScript 介面
docs/                  # 架構和訊息協定
deliverables/          # 已有 planning report，保留作項目參考
data/                  # 本機資料庫；不加入 Git
```

技術棧：Python、FastAPI、Pydantic、SQLAlchemy、SQLite／PostgreSQL、Argon2id、WebSockets、React、TypeScript、Vite、pytest、Vitest、Docker。詳細設計見 [architecture](docs/architecture.md)、[message protocol](docs/protocol.md) 和 [database](docs/database.md)。

## 上傳 GitHub

先在 GitHub 建立空 repository，然後把你的 repository URL 填入以下指令：

```bash
git status
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin HEAD
```

如果已設定 `origin`，先用 `git remote -v` 查看，不要重複新增。這份專案不包含 API keys、環境 secrets、`node_modules` 或本機實驗資料庫。

## 後續版本

後續可加入其他 programmed strategies、更多 auction mechanisms 和 LLM adapters。目前尚未包含手動投標、第二價格／動態拍賣、LLM 呼叫、遠端自訂程式執行、電郵驗證或忘記密碼流程。這些擴充應沿用 receiver、帳戶權限及受驗證的訊息協定，並補上各自的規則測試。
