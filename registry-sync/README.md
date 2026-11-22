# registry-sync

定期把 dn42 registry（`git.dn42.dev/dn42/registry`）同步進 PostgreSQL 的 `registry` schema。

```
每 REGISTRY_SYNC_INTERVAL 秒（或收到 POST /sync）：
  git fetch --depth=1 → 和 registry.meta.commit 比對
    ├─ 相同 → 只更新 synced_at
    └─ 不同 → reset --hard → 解析 data/<type>/* → 單一 transaction 替換全部資料表
```

- 全量匯入只需數秒；registry 沒有變動時不會重新匯入。
- 匯入使用 `DELETE` + `COPY`，進行中其他連線不會被擋，也仍讀得到舊資料。
- checkout 物件數低於 `REGISTRY_MIN_OBJECTS` 時拒絕匯入，避免壞掉的 checkout 清空資料庫。

## `registry` schema 規則

- schema 本身由 CNPG 的 `Database` 資源建立，owner 是 `registry_sync`（見 `deploy/base/postgres.yaml`）。schema 不存在時 worker 會報錯，不會自己建立。
- schema 裡的資料表由本服務管理，是**可重建的鏡像**。`SCHEMA_VERSION` 改變時，刪除 schema 內所有資料表後重建，再全量匯入。
- `REGISTRY_READER_ROLES`（預設 `autopeer`）只會拿到 `USAGE` + `SELECT`，每次啟動都會重新授權。不要在這個 schema 建立物件，也不要對這些表建 foreign key，業務資料表請用純文字存 ASN / MNT。
- backend 的 alembic autogenerate 需要排除 `registry` schema。

| 表 | 內容 |
|---|---|
| `meta` | 目前的 commit、commit 時間、最後確認時間、物件數 |
| `object` | 全部物件：`type`、`key`（檔名）、`attrs`（`{"key": ["value", ...]}`）、`raw` |
| `object_mnt` | 任何物件的 `mnt-by`（大寫） |
| `object_contact` | 任何物件的 `admin-c` / `tech-c`（大寫） |
| `aut_num` | `asn`（bigint）、`as_name` |
| `mntner_auth` | `method` = `pgp-fingerprint` / `pgp-key-cert` / `ssh-ed25519` / ...，`value` 為正規化後的值 |
| `key_cert` | `auth: PGPKEY-xxxx` 指向的公鑰（`fingerprint`、armored `certif`） |
| `contact_email` | person / role 的 `e-mail` |
| `inetnum` | inetnum + inet6num，`prefix` 為 `cidr`（GiST index，可用 `>>=` 查包含） |
| `route` | route + route6，每個 origin 一列 |

## 怎麼讀取

連線資訊：

| | |
|---|---|
| 位址 | `postgres-rw.dn42-autopeer.svc:5432` |
| 資料庫 | `autopeer` |
| 帳號 / 密碼 | Secret `postgres-app` 的 `username` / `password`（`autopeer`，只能 `SELECT`） |
| 資料位置 | `registry` schema |

- 先查 `registry.meta` 確認資料同步到哪個 commit、最後確認時間（`synced_at`）。
- 需要立即同步（例如使用者剛改完 registry）時，呼叫 `POST http://registry-sync.dn42-autopeer.svc/sync`，再輪詢 `GET /status` 直到 `commit` 改變。
- mnt、handle 一律存成大寫，查詢時請先轉大寫。

### 常用查詢

ASN 屬於哪些 MNT，以及各 MNT 的驗證方式：

```sql
SELECT m.mnt, a.method, a.value
FROM registry.aut_num n
JOIN registry.object_mnt m ON m.type = 'aut-num' AND m.key = n.key
JOIN registry.mntner_auth a ON a.mnt = m.mnt
WHERE n.asn = 4242422020
ORDER BY m.mnt, a.idx;
```

`method = 'pgp-key-cert'` 時，公鑰在 `key_cert`：

```sql
SELECT k.fingerprint, k.certif
FROM registry.mntner_auth a
JOIN registry.key_cert k ON k.key = a.value
WHERE a.mnt = 'XUU-MNT' AND a.method = 'pgp-key-cert';
```

MNT 可用的 email（只取 `mnt-by` 為同一個 MNT 的 person / role）：

```sql
SELECT DISTINCT e.email
FROM registry.object_contact c
JOIN registry.contact_email e ON e.handle = c.handle
JOIN registry.object_mnt pm ON pm.type = e.type AND pm.key = c.handle AND pm.mnt = c.key
WHERE c.type = 'mntner' AND c.key = 'REDBEAN0721-MNT';
```

IP 落在哪個網段、由誰維護（最精確的在最前面）：

```sql
SELECT i.prefix, i.netname, m.mnt
FROM registry.inetnum i
JOIN registry.object_mnt m ON m.type = i.type AND m.key = i.key
WHERE i.prefix >>= '172.20.45.10'::inet
ORDER BY masklen(i.prefix) DESC;
```

ROA：prefix 允許由哪些 origin 宣告：

```sql
SELECT prefix, origin, coalesce(max_length, masklen(prefix)) AS max_length
FROM registry.route
WHERE prefix >>= '172.20.45.0/26'::cidr;
```

沒有整理表的類型（as-set、dns、organisation 等）直接查 `object.attrs`：

```sql
SELECT attrs -> 'nserver' FROM registry.object WHERE type = 'dns' AND key = 'redbean0721.dn42';
```

## HTTP

不經過 Ingress，只有叢集內部連得到。GET 請求不寫入 log。

| | |
|---|---|
| `GET /healthz` | liveness：同步迴圈沒有卡住（不檢查 DB，DB 暫時無法連線時 worker 會自行重試） |
| `GET /readyz` | readiness：此 process 已成功同步過至少一次 |
| `GET /status` | JSON 狀態（commit、最後錯誤等） |
| `POST /sync` | 要求立即同步；冷卻時間內回 `429` + `Retry-After` |

## 設定（環境變數）

| 變數 | 預設 | 說明 |
|---|---|---|
| `REGISTRY_REPO_URL` | `https://git.dn42.dev/dn42/registry.git` | |
| `REGISTRY_BRANCH` | `master` | |
| `REGISTRY_GIT_USERNAME` / `REGISTRY_GIT_TOKEN` | — | **必填**，registry 需要登入，缺少或為空時啟動即失敗。只透過 credential helper 傳給 git，不會寫到磁碟 |
| `REGISTRY_WORKDIR` | `/data/registry` | clone 的位置 |
| `REGISTRY_DATABASE_URL` | 空 | 空字串時使用 libpq 的 `PGHOST` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` |
| `REGISTRY_READER_ROLES` | `["autopeer"]` | 授予唯讀權限的角色（JSON 陣列） |
| `REGISTRY_SYNC_INTERVAL` | `300` | 秒 |
| `REGISTRY_RETRY_INTERVAL` | `60` | 失敗後的重試間隔 |
| `REGISTRY_TRIGGER_COOLDOWN` | `30` | `POST /sync` 的冷卻時間 |
| `REGISTRY_MIN_OBJECTS` | `1000` | |
| `REGISTRY_HTTP_PORT` | `8080` | |

## 開發

```sh
uv sync
docker run -d --rm --name rs-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:18-alpine
# TEST_DATABASE_URL 要用超級使用者：測試會建立 registry_sync / autopeer 角色並模擬 CNPG 建好的 schema
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy .

# 實際同步一次
REGISTRY_WORKDIR=/tmp/registry REGISTRY_GIT_USERNAME=... REGISTRY_GIT_TOKEN=... \
REGISTRY_DATABASE_URL=postgresql://registry_sync:test@127.0.0.1:55432/postgres \
uv run python -m registry_sync --once
```
