# 12-ladderbill（阶梯电费）

Ladderbill — 居民阶梯电价分段累进（含尖峰系数）

## 启动

```bash
docker compose up --build -d
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:4100 |
| API | http://localhost:9100 |

backend 已配置健康探针（探测 `/api/health`），frontend 会在 backend 健康后再启动。

## API 冒烟链路

后端就绪后依次校验三个只读端点：health → 户列表 → 默认档表列表。
脚本仅依赖 Python 3 标准库，不需要前端构建产物，也不需要任何密钥，可重复执行。

```bash
# 1. 启动（健康探针通过后 frontend 才启动）
docker compose up --build -d

# 2. 等待健康探针就绪并跑三步冒烟（任一失败返回非零并打印失败步骤与响应摘要）
python3 scripts/smoke_api.py
```

成功输出示例：

```text
[..] 等待 backend 健康探针就绪 (BASE_URL=http://127.0.0.1:9100, 超时 60s)
[ok] docker 报告 backend 健康 (health=healthy)
[PASS] 1/3 health 健康检查: HTTP 200 — ok=true, project='ladderbill'
[PASS] 2/3 accounts 户列表: HTTP 200 — 共 2 户，首个: id=1 name='张家'
[PASS] 3/3 tiers 默认档表列表: HTTP 200 — 共 3 档，首档: id=1 price=0.52
[DONE] API 冒烟全部通过 (3/3)
```

可用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BASE_URL` | `http://127.0.0.1:9100` | API 基地址，端口映射不同或远端部署时覆盖 |
| `SMOKE_READY_TIMEOUT` | `60` | 等待健康探针就绪的最长秒数 |
| `SMOKE_READY_INTERVAL` | `1` | 就绪轮询间隔秒数 |
| `SMOKE_STEP_TIMEOUT` | `5` | 单步请求超时秒数 |

### 停止 / 恢复演练（验证快速失败与幂等）

```bash
# 停止后端：脚本通过 docker compose ps 发现容器已退出，立即快速失败（无需等满超时）
docker compose stop backend
python3 scripts/smoke_api.py        # 非零退出，提示 "backend 容器已停止 (state=exited)"

# 恢复 compose：不改任何代码再次执行，三步连续成功
docker compose start backend
python3 scripts/smoke_api.py        # 3/3 通过
```

> 说明：脚本不内嵌任何 compose 之外的密钥。docker 不可用或 `BASE_URL` 指向非本机地址时，
> 容器状态探测自动退化为对 `/api/health` 的有界 HTTP 轮询。

## 主链

抄表录入 → 阶梯分段计费 → 账单明细

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。
