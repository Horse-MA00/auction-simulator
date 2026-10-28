# 部署到 Render

目前 repository 的 `render.yaml` 會建立一個 Docker Web Service（React frontend + FastAPI backend）和一個 PostgreSQL database，兩者均在 Singapore。既有 Dockerfile 已負責 build frontend，不需要另外建立 Static Site 或填 Python build command。

這份 Blueprint 使用付費入門 compute：Web Service `0.5c-512mb` 約 US$7/月，PostgreSQL `0.1c-256mb` 約 US$6/月。總計約 US$13/月起，額外流量、儲存及稅費以 Render 的實際報價為準。單純把設定檔放在 GitHub 不會建立服務或產生費用；在 Render 確認建立前請查看頁面上的費用。

## 1. 從 GitHub 建立 Blueprint

1. 登入 [Render Dashboard](https://dashboard.render.com/)。
2. 選擇 **New → Blueprint**。
3. 連接 GitHub，選擇 `Horse-MA00/auction-simulator`。若看不到 repository，調整 Render GitHub App 的 repository access。
4. Branch 選擇 **`auction-simulator-v0`**；Blueprint Path 使用 **`render.yaml`**。
5. 設定 Blueprint 名稱，例如 `han2601`。
6. 檢查預覽：`han2601-auction` Web Service 和 `han2601-postgres` database。確認地區、方案及費用後才按建立／Deploy Blueprint。

若你已手動建立其他同名服務，先核對它們的用途和資料，避免重複建立。不要為了套用 Blueprint 刪除已有資料的 database。

## 2. 等待部署完成

Render 會先建立 PostgreSQL，然後按 Dockerfile build 並啟動網站。等待 `han2601-auction` 顯示 **Live**，開啟它實際分配的 `https://....onrender.com` 網址。

環境變數已由 Blueprint 設定：

| 變數 | 用途 |
| --- | --- |
| `AUCTION_DATABASE_URL` | 自動引用 PostgreSQL 的 Internal Database URL |
| `AUCTION_COOKIE_SECURE=true` | 登入 cookie 只經 HTTPS 傳送 |
| `AUCTION_ALLOWED_ORIGINS` | 自動引用此 Web Service 的 `RENDER_EXTERNAL_URL` |
| `AUCTION_FRONTEND_DIR=/app/frontend/dist` | 使用 Docker build 好的 frontend |
| `PORT=8000` | 配合現有 Dockerfile 的監聽 port |

資料庫連線使用內部網路；資料庫不開放公網。帳戶和拍賣資料儲存在 PostgreSQL，不依賴 Web Service 的暫存檔案系統。本機 SQLite 的舊資料不會自動搬到 Render。

## 3. 建立第一個管理員

在 **`han2601-auction` Web Service → Shell** 執行：

```bash
python -m backend.auction_sim.admin create admin
```

按提示輸入兩次自訂密碼（10–128 字元；輸入時不顯示）。不要把密碼放在 command arguments、GitHub 或本文件。

完成後，用 `admin` 和剛設定的密碼在網站登入。其他人可透過網站註冊；admin 建立拍賣時，再於 bidder 的 **Assigned account** 選單指派帳戶。出價仍由目前的 80% scripted strategy 執行。

若想將已註冊的帳戶提升為管理員，在同一個 Shell 執行：

```bash
python -m backend.auction_sim.admin promote YOUR_USERNAME
```

提升權限後，該帳戶需重新登入。不要在本機預設 SQLite 執行這個命令來設定雲端 admin，兩者是不同資料庫。

## 4. 驗證與後續更新

- 開啟網站的 `/api/health`，應顯示 `{"status":"ok"}`。
- 註冊一個普通帳戶，再用 admin 建立並指派 bidder；切回普通帳戶確認只能看到自己的出價。
- 重新部署後確認帳戶和拍賣仍存在。
- 後續將程式 push 到 `auction-simulator-v0`，由 Render 的 auto-deploy 設定決定是否自動部署；也可在 Dashboard 使用 Manual Deploy。
- 想用自訂域名時，先在 Render Settings → Custom Domains 完成 DNS 和 TLS 設定，再把新 origin 加入 `AUCTION_ALLOWED_ORIGINS`。修改 Blueprint 中此變數的來源，改為明確列出逗號分隔的實際 HTTPS origins，避免下一次 Blueprint sync 覆蓋手動設定。

## 常見問題

**登入出現 `Request origin is not allowed`：** 在 Web Service 的 Environment 確認 `AUCTION_ALLOWED_ORIGINS` 等於你瀏覽器網址的 `https://主機名稱`，不要包含路徑或結尾斜線。若改過服務名稱或域名，重新同步 Blueprint 並檢查此值。

**Build 找不到 Dockerfile：** 確認使用 `auction-simulator-v0` branch、repository 根目錄，以及 `./Dockerfile`，不要選 frontend 子目錄。

**Database connection failed：** 確認 database 已可用、兩個服務同區，且 `AUCTION_DATABASE_URL` 使用 Blueprint 引用的 Internal Database URL。

**普通使用者看到空列表：** 管理員尚未把 bidder 指派給這個帳戶。這不是登入錯誤。

**找不到 Shell：** Free Web Service 不提供 Shell。本文件採用付費 Web Service，因此可在服務內直接執行 admin CLI。

## 免費試跑的差異

Render 的 Free Web Service 閒置 15 分鐘後會休眠；Free PostgreSQL 在建立 30 天後到期；Free Web Service 也沒有 Shell。這些不適合直接套用以上長期部署及 admin 建立流程。若只想免費試跑，需要另外安排可從本機安全連線的資料庫來執行 admin CLI，或選擇支援持久資料庫的其他方案；不要把 production 資料改回 Web Service 上的 SQLite。

參考：[Blueprint setup](https://render.com/docs/infrastructure-as-code)、[Blueprint schema](https://render.com/docs/blueprint-spec)、[Default environment variables](https://render.com/docs/environment-variables)、[Docker](https://render.com/docs/docker)、[Pricing](https://render.com/pricing)、[Free limits](https://render.com/docs/free)。
