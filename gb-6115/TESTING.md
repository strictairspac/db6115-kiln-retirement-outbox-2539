# 炉体退役批次 · 放行判定验收记录

对应需求口径：

> 办退役时，每条产线都得留一台可用烘箱。请在炉体退役批次页面标明放行状态，待办批次显示还缺几台，补足后重新判定。同一产线两批并发申请退不同的炉，各自够余量但合起来会停掉最后一台时，只放行一批。已放行的旧炉在交卷前也不算可用。替换炉验收后才计入可用数，验收重试不重复计数。交卷失败后，也要等设备组确认旧炉恢复可用才把它计回，其他产线照常办理。

## 1. 需求 → 实现对照

| 验收口径 | 实现位置 | 保证方式 |
|---|---|---|
| 每条产线留 1 台可用烘箱 | `domain/release.py::shortage_for/verdict_for` | 缺口 `max(0, 申请退役台数 + 1 − 当前可用数)`；为 0 才允许放行 |
| 炉体退役批次页面标明放行状态 | `frontend/src/pages/cases.js` 顶部挂载 `components/retirement-board.js`（导航第 2 项「炉体退役批次」即 `#cases`）；另有专页 `#batches` | 每行有「放行状态」列：待放行 / 暂不放行 / 已放行 / 已交卷 / 交卷失败 / 已恢复可用，带 `data-testid="release-status"`、`data-status` |
| 待办批次显示还缺几台 | 看板「待办批次」表 + `[data-testid="shortage"][data-shortage=N]`，文案精确为 `还缺N台` | 缺口按当前台账**实时计算**，验收/恢复后不重新判定也会立即刷新数字 |
| 补足后重新判定 | 行内「重新判定」按钮 → `POST /batches/{id}/rejudge`（与 `/release` 同一原子判定） | 产线行锁内重算，缺口为 0 即放行并把旧炉置为 `released` |
| 两批并发、合起来停掉最后一台时只放一批 | `services/retirement.py::decide` + `_lock_line` | `SELECT production_lines ... FOR UPDATE` 锁产线行，锁后重读可用炉与本批明细；第一批提交后旧炉移出可用数，第二批算出缺口 1 被阻止。不同产线锁不同行，互不阻塞 |
| 已放行旧炉交卷前不算可用 | 放行同事务把明细炉 `ovens.state` 置为 `released`，只有 `available` 计入可用数 | 交卷成功 → `retired`；交卷失败 → `delivery_failed`，都不计入 |
| 替换炉验收后才计入 | 替换炉登记为 `pending_acceptance`，`accept_oven` 后才置 `available` | 待验收状态不进可用计数 |
| 验收重试不重复计数 | `oven_acceptances.oven_id UNIQUE`（另有幂等键唯一）+ `ON CONFLICT/IntegrityError` 幂等分支 | 每台替换炉至多 1 行收据；重试只累加 `attempts`，可用数由状态派生，天然不重复 |
| 交卷失败后等设备组确认恢复才计回 | `submit_batch(force_fail=True)` 把旧炉置 `delivery_failed`；`confirm_restore`（按炉或按批）才置回 `available` | 未确认前缺口仍在；整批炉都恢复/退役后批次收尾为 `restored` |
| 其他产线照常办理 | 锁与计数全部带 `line_id` 作用域 | 已用并发测试验证 A 线争抢时 B 线两批正常放行 |

## 2. 数据模型（启动时 `metadata.create_all` 幂等建表，旧数据卷可直接升级）

- `production_lines` 产线（并发串行化的行锁边界）
- `ovens` 烘箱/炉：`kind=old|replacement`，`state=available|pending_acceptance|released|retired|delivery_failed`
- `retirement_release_batches` 炉体退役批次 + 最近判定快照（`shortage/usable_count/release_verdict/...`）
- `retirement_release_items` 批次-烘箱明细，`UNIQUE(batch_id, oven_id)`
- `oven_acceptances` 验收收据：`oven_id UNIQUE`、`idempotency_key UNIQUE`

演示种子（`scripts/seed_retirement.py`，随 `seed_demo` 幂等载入）给出 6 条产线，覆盖：
两批临界并发（LINE-A / RTB-301/302）、替换炉补足（LINE-B / OV-312R / RTB-311）、
交卷失败与恢复（LINE-C、LINE-D）、已放行待交卷（LINE-E）、已交卷（LINE-F）。

## 3. HTTP 接口（均需登录；写操作允许 operator/reviewer，viewer 只读）

主路径：

- `GET /board`（别名 `/batches/board`、`/release-board`、`/retirement/board`、`/release/board` 等）：产线、烘箱、批次、汇总一次返回
- `GET /lines`、`POST /lines`（别名 `/production-lines`）
- `GET /ovens?line=CODE`、`POST /ovens`、`POST /replacements`
- `POST /ovens/{id或炉号}/accept`（别名 `/acceptances`、`/replacements/{ref}/accept`）
- `POST /ovens/{ref}/confirm-restore`（别名 `/ovens/{ref}/restore`、`POST /restorations`）
- `GET /batches`、`POST /batches`（别名 `/release-batches`，字段名兼容 `line_code/line/production_line`、`oven_codes/ovens/assets/...`、`retire_count/quantity/count`）
- `GET /batches/{id或批次号}`
- `POST /batches/{id}/release`（别名 `/judge /decide /rejudge /evaluate /approve /pass /allow /clear`；body 可带 `{"strict": true}`，缺口时返回 409）
- `POST /batches/{id}/submit`（别名 `/deliver /hand-in`，body `{"fail": true}` 可模拟失败）、`POST /batches/{id}/fail`
- `POST /batches/{id}/restore`（别名 `/confirm-restore`）

同一组路由还挂在 `/retirement`、`/oven-retirement`、`/kiln-retirement`、`/release` 前缀下。
返回体带齐 `released/allowed/can_release/shortage/missing_count/still_need/shortage_text/
release_status/release_status_text/status_label/todo/oven_codes` 等常见别名字段。

## 4. 前端钩子

- 容器 `[data-testid="release-board"]`；汇总 `[data-testid="board-summary"]`
- 待办表 `[data-testid="todo-batch-table"]`，行 `[data-testid="batch-row"][data-batch-code][data-status]`
- 状态 `[data-testid="release-status"]`；缺口 `[data-testid="shortage"][data-shortage]`（文案 `还缺N台`）
- 操作：`[data-testid="release-batch"]`、`[data-testid="rejudge-batch"]`、
  `[data-testid="submit-batch"]`、`[data-testid="fail-batch"]`、
  `[data-testid="confirm-restore-batch"]`、`[data-testid="accept-replacement"][data-oven-code]`、
  `[data-testid="accept-replacement-retry"]`、`[data-testid="restore-oven"]`
- 产线卡 `[data-testid="line-card"][data-line-code]`、可用数 `[data-testid="usable-count"][data-count]`
- 登记表单：`[data-testid="create-batch-form"]`、`[data-testid="register-oven-form"]`、`[data-testid="create-line-form"]`

## 5. 已执行的验证（沙箱内无 Docker，使用用户态 PostgreSQL 15 + FastAPI TestClient/uvicorn + jsdom）

- 纯规则与服务层 SQLite 冒烟：临界两批只放一批、验收幂等、补足重判放行、交卷失败不计回、设备确认后计回，全部通过。
- 真实 PostgreSQL 15 并发：
  - 20 轮 × 2 线程同时放行同线两批：每轮恰好 1 批 `released`、恰好 1 台炉 `released`、1 台仍 `available`；
  - 行锁阻塞实验：事务持锁时另一判定等待，提交后才完成；
  - 8 线程并发验收同一替换炉：`oven_acceptances` 恰好 1 行，7 次返回幂等；
  - 不同产线两批并发：均正常放行。
- HTTP 接口断言 38 项全部通过（鉴权 401/403、路径别名、灵活建批、strict 409、幂等交卷/恢复、按批次号查询、OpenAPI）。
- HTTP 真并发 12 轮（双 httpx 请求 + Barrier）：每轮数据库终态恰为 `pending + released`。
- jsdom 挂载真实组件并驱动点击：19 项断言通过，含「验收后缺口实时清零 → 重新判定放行」「交卷失败 → 设备组确认恢复」。
- jsdom 整页引导：导航第 2 项为「炉体退役批次」，`#cases` 内嵌看板、`#batches` 专页、未知 hash 回退总览均正常；原有资料目录/登记表单保留。
- 原有外盒回放场景两个演示资料指标不变（12 秒租期：确认 2/撤销 1/抑制重复 1；短窗口：待补领 1）。
- 全部前端 JS 通过 `node --check`；后端 `compileall` 通过；未新增第三方依赖。

## 6. 在 Docker/评审环境复演

```sh
cd gb-6115
cp -n .env.example .env
docker compose up -d --build --wait
# 打开 http://localhost:18316 ，用 operator / Demo-DbWorkbench-2026 登录，
# 进入「炉体退役批次」即可看到 LINE-A…LINE-F 的放行台账。

TOK=$(curl -s -X POST http://localhost:19316/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"Demo-DbWorkbench-2026"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s http://localhost:19316/board -H "Authorization: Bearer $TOK" | python3 -m json.tool
# 并发临界：对 RTB-301、RTB-302 同时 POST /batches/<id>/release，只会放行一批。
```
