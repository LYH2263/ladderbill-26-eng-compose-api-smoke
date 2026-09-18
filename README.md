# 12-ladderbill（阶梯电费）

Ladderbill — 居民阶梯电价分段累进（含尖峰系数）

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:4100 |
| API | http://localhost:9100 |

## 主链

抄表录入 → 阶梯分段计费 → 账单明细

## API 冒烟检查

backend 服务在 compose 中配置了健康探针（`GET /api/health`，`docker compose ps` 可见 healthy 状态）。冒烟脚本 `scripts/smoke_api.py` 待健康探针就绪后，依次请求 **health → 户列表 → 默认档表列表** 三个端点，逐步校验 HTTP 2xx 与 JSON 必要字段；任一步失败即打印失败步骤名与响应摘要并以非零退出。

```bash
docker compose up -d --build        # 启动全部服务
python3 scripts/smoke_api.py        # 默认打 http://localhost:9100（即 compose 的 9100 映射）

# 端口映射不同时用 BASE_URL 覆盖（如 compose 改为 19100:9100）：
BASE_URL=http://localhost:19100 python3 scripts/smoke_api.py
```

- 仅依赖 Python 3 标准库、只发 GET 请求，可在干净 shell 重复执行，不依赖前端构建产物。
- 可选环境变量：`SMOKE_WAIT_SECONDS`（等待健康探针就绪上限，默认 10 秒）、`SMOKE_REQ_TIMEOUT`（单请求超时，默认 3 秒）。
- 快速失败与恢复验证：`docker compose stop backend` 后执行脚本，会在健康探针等待上限内以非零退出；`docker compose up -d` 恢复后无需改代码再次执行，三步连续成功。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
