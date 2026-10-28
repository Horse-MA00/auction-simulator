# HAN2601 Auction Simulator

根據 *A Generic Auction Simulator for Programmed and LLM-Based Agents* proposal 製作的 v0。這個版本以 scripted auctioneer、scripted bidders 和中央 receiver，完整執行一場單件物品的 **first-price sealed-bid auction**。不需要 LLM API key。

## v0 可以做甚麼

- 設定物品、底價、bidder 估值和預算。
- 逐步執行或自動播放：開標、各 bidder 提交、關閉投標、結算。
- 由 receiver 檢查訊息來源、session、round、次序及合法性。
- 計算得標者、付款、賣方收入和 bidder surplus。
- 用 SQLite 保留 session 和 event log；重新啟動後可以繼續。
- 透過 WebSocket 更新事件、逐事件重播，並匯出已完成實驗的 JSON。

v0 是本機、單一研究者使用的原型；介面和 API 沒有登入系統。API 和匯出供研究者檢查實驗，bidder 則只會收到獨立的受限 observation。預設啟動及 Docker port 都綁定 `127.0.0.1`。

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

例如估值為 100、預算為 100，固定出價為 80；如果得標，效用為 `100 - 80 = 20`，即估值的 20%。這不是對所有拍賣機制或所有策略的獲利保證。密封出價在結算前不向其他 bidders 公開；研究介面也會隱藏未結算的出價。

## 本機啟動

需要 Python 3.11 或以上、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及 Node.js 22（22.12 或以上）和 npm。請在 repository 根目錄執行。

先安裝並啟動 backend：

```bash
uv sync --frozen
uv run uvicorn backend.auction_sim.main:app --host 127.0.0.1 --port 8000 --reload
```

再開另一個 terminal 啟動 frontend：

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

開啟 [http://127.0.0.1:5173](http://127.0.0.1:5173)。Vite 會將 `/api` 和 WebSocket 請求轉到 port 8000。Backend 的互動 API 文件位於 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。

亦可先 build frontend，再由 FastAPI 同時提供 API 和網頁：

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv run uvicorn backend.auction_sim.main:app --host 127.0.0.1 --port 8000
```

這時直接開啟 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

## 一個 API 實驗

```bash
curl -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"item_name":"Demo lot","reserve_price":50,"bidders":[{"id":"alice","name":"Alice","valuation":100,"budget":100},{"id":"bob","name":"Bob","valuation":90,"budget":100}]}'
```

把回傳的 `id` 代入下一個指令，即可自動執行到結算：

```bash
curl -X POST http://127.0.0.1:8000/api/runs/RUN_ID/execute
```

這個例子會由 Alice 以 80 credits 得標，效用為 20；Bob 支付 0。若想逐步觀察，使用 `/advance` 代替 `/execute`。

## 測試

```bash
uv run pytest
npm --prefix frontend test
npm --prefix frontend run build
```

測試涵蓋結算、底價、同價、預算、訊息驗證、密封出價、重播和持久化。GitHub Actions 會執行 backend tests、frontend tests 和 frontend production build。

## Docker

需要啟動 Docker Desktop 或 Docker Engine：

```bash
docker compose up --build
```

開啟 [http://127.0.0.1:8000](http://127.0.0.1:8000)。SQLite 保存在 named volume `auction-data`，重建 container 不會清除實驗。`docker compose down` 會停止服務並保留 volume。

## 設定與目錄

| 環境變數 | 預設／用途 |
| --- | --- |
| `AUCTION_DB_PATH` | `data/auction.sqlite3`，SQLite 檔案位置 |
| `AUCTION_FRONTEND_DIR` | `frontend/dist`，已 build 的 frontend 位置 |

`.env.example` 提供參考值。Backend 讀取 process environment，不會自行載入 `.env`；需要更改時，可在啟動指令前設定，例如 `AUCTION_DB_PATH=data/demo.sqlite3 uv run uvicorn backend.auction_sim.main:app`。

```text
backend/auction_sim/   # API、receiver、rules、agents、SQLite storage
backend/tests/         # Backend tests
frontend/              # React + TypeScript 介面
docs/                  # 架構和訊息協定
deliverables/          # 已有 planning report，保留作項目參考
data/                  # 本機資料庫；不加入 Git
```

技術棧：Python、FastAPI、Pydantic、SQLite、WebSockets、React、TypeScript、Vite、pytest、Vitest、Docker。詳細設計見 [architecture](docs/architecture.md) 和 [message protocol](docs/protocol.md)。

## 上傳 GitHub

先在 GitHub 建立空 repository，然後把你的 repository URL 填入以下指令：

```bash
git status
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin HEAD
```

如果已設定 `origin`，先用 `git remote -v` 查看，不要重複新增。這份專案不包含 API keys、環境 secrets、`node_modules` 或本機實驗資料庫。

## 後續版本

後續可加入其他 programmed strategies、更多 auction mechanisms 和 LLM adapters。v0 尚未包含第二價格／動態拍賣、LLM 呼叫、遠端自訂程式執行或多使用者權限。這些擴充應沿用 receiver 和受驗證的訊息協定，並補上各自的規則測試。
