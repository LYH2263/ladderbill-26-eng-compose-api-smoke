#!/usr/bin/env python3
"""Ladderbill API 冒烟脚本。

在 backend 健康探针就绪后，依次请求三个端点：
  1. GET /api/health    健康检查
  2. GET /api/accounts  户列表
  3. GET /api/tiers     默认档表列表

每一步校验 HTTP 2xx 与 JSON 体必要字段；任一步失败即以非零退出，
并打印失败步骤名与响应摘要。全部为只读 GET，可在干净 shell 重复执行，
不依赖前端构建产物，也不需要任何密钥。

环境变量：
  BASE_URL               API 基地址，默认 http://127.0.0.1:9100（compose 映射端口）
  SMOKE_READY_TIMEOUT    等待健康探针就绪的最长秒数，默认 60
  SMOKE_READY_INTERVAL   就绪轮询间隔秒数，默认 1
  SMOKE_STEP_TIMEOUT     单步请求超时秒数，默认 5
  COMPOSE_FILE           docker compose 文件路径，默认 <仓库根>/docker-compose.yml
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:9100").rstrip("/")
READY_TIMEOUT = float(os.environ.get("SMOKE_READY_TIMEOUT", "60"))
READY_INTERVAL = float(os.environ.get("SMOKE_READY_INTERVAL", "1"))
STEP_TIMEOUT = float(os.environ.get("SMOKE_STEP_TIMEOUT", "5"))

DEFAULT_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
COMPOSE_FILE = os.environ.get("COMPOSE_FILE", str(DEFAULT_COMPOSE_FILE))

HEALTH_PATH = "/api/health"
SUMMARY_LIMIT = 300
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 这些状态表示容器不会自行恢复，直接快速失败而不是等满超时
STOPPED_STATES = {"exited", "dead", "removing", "paused"}


def log(msg: str) -> None:
    print(msg, flush=True)


def summarize(raw: bytes | str | None) -> str:
    """生成单行响应摘要，超长截断。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = raw
    text = " ".join(text.split())
    if len(text) > SUMMARY_LIMIT:
        text = text[:SUMMARY_LIMIT] + "…(截断)"
    return text


def fail(step: str, detail: str, status: int | None = None,
         body: bytes | None = None, error: str | None = None) -> None:
    """打印失败步骤信息并以非零退出。"""
    log(f"[FAIL] 失败步骤: {step}")
    if detail:
        log(f"  原因: {detail}")
    if status is not None:
        log(f"  HTTP 状态: {status}")
    if error:
        log(f"  网络错误: {error}")
    summary = summarize(body)
    if summary:
        log(f"  响应摘要: {summary}")
    sys.exit(1)


def http_get(path: str) -> tuple[int | None, bytes, str | None]:
    """发起 GET，返回 (HTTP 状态码, 响应体, 网络错误)。

    4xx/5xx 仍会带回状态码与响应体；连接被拒/超时等才返回 error。
    """
    url = BASE_URL + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=STEP_TIMEOUT) as resp:
            try:
                return resp.status, resp.read(), None
            except http.client.IncompleteRead as exc:
                return resp.status, exc.partial, f"{url} -> 响应被截断: {exc}"
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read(), None
        except Exception:
            return exc.code, b"", None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, b"", f"{url} -> {exc}"


def parse_json(step: str, status: int | None, body: bytes, error: str | None) -> dict:
    if error is not None and status is None:
        fail(step, "请求未到达后端（连接被拒/超时）", status, body, error)
    if not 200 <= (status or 0) < 300:
        fail(step, "期望 HTTP 2xx", status, body, error)
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(step, f"响应体不是合法 JSON: {exc}", status, body, error)
    if not isinstance(data, dict):
        fail(step, f"响应 JSON 顶层应为对象，实际为 {type(data).__name__}", status, body)
    return data


def require_keys(step: str, obj: dict, keys: list[str], status: int, body: bytes,
                 where: str = "响应体") -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        fail(step, f"{where}缺少必要字段: {', '.join(missing)}", status, body)


# ---- 各步骤的响应体校验 ----------------------------------------------------

def check_health(data: dict, status: int, body: bytes) -> str:
    step = "health 健康检查"
    require_keys(step, data, ["ok", "project"], status, body)
    if data.get("ok") is not True:
        fail(step, f"ok 应为 true，实际为 {data.get('ok')!r}", status, body)
    return f"ok=true, project={data.get('project')!r}"


def check_accounts(data: dict, status: int, body: bytes) -> str:
    step = "accounts 户列表"
    require_keys(step, data, ["items"], status, body)
    items = data["items"]
    if not isinstance(items, list):
        fail(step, f"items 应为数组，实际为 {type(items).__name__}", status, body)
    if not items:
        fail(step, "items 为空：种子户列表不应为空", status, body)
    first = items[0]
    require_keys(step, first, ["id", "name", "meter_no"], status, body,
                 where="items[0]")
    return f"共 {len(items)} 户，首个: id={first['id']} name={first['name']!r}"


def check_tiers(data: dict, status: int, body: bytes) -> str:
    step = "tiers 默认档表列表"
    require_keys(step, data, ["items"], status, body)
    items = data["items"]
    if not isinstance(items, list):
        fail(step, f"items 应为数组，实际为 {type(items).__name__}", status, body)
    if not items:
        fail(step, "items 为空：默认档表不应为空", status, body)
    first = items[0]
    require_keys(step, first, ["id", "price", "sort_order"], status, body,
                 where="items[0]")
    return f"共 {len(items)} 档，首档: id={first['id']} price={first['price']}"


STEPS = [
    ("health 健康检查", HEALTH_PATH, check_health),
    ("accounts 户列表", "/api/accounts", check_accounts),
    ("tiers 默认档表列表", "/api/tiers", check_tiers),
]


# ---- backend 容器状态探测（用于“容器停止时快速失败”） ----------------------

def docker_backend_state() -> tuple[str | None, str | None, int | None]:
    """通过 docker compose 查询 backend 容器状态。

    返回 (state, health, exit_code)；docker 不可用 / daemon 未运行 /
    查询失败时返回 (None, None, None)，调用方退化为纯 HTTP 轮询。
    服务从未创建时 state 为 "absent"。
    """
    docker = shutil.which("docker")
    if not docker:
        return None, None, None
    try:
        proc = subprocess.run(
            [docker, "compose", "-f", COMPOSE_FILE, "ps", "backend",
             "--format", "json"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None, None, None
    if proc.returncode != 0 or "Cannot connect to the Docker daemon" in proc.stderr:
        return None, None, None

    out = proc.stdout.strip()
    if not out:
        return "absent", None, None

    # 兼容按行 JSON 对象（旧版 compose）与 JSON 数组（新版 compose）
    rows: list[dict] = []
    try:
        parsed = json.loads(out)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)

    if not rows:
        return "absent", None, None
    row = rows[0]
    state = (row.get("State") or row.get("state") or "").lower() or None
    health = (row.get("Health") or row.get("health") or "").lower() or None
    exit_code = row.get("ExitCode", row.get("exit_code"))
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    return state, health, exit_code


def is_local_base_url() -> bool:
    host = (urlparse(BASE_URL).hostname or "").lower()
    return host in LOCAL_HOSTS


def wait_until_ready() -> None:
    """等待 backend 健康探针通过；容器已停止则快速失败。"""
    step = "等待 backend 健康探针就绪"
    deadline = time.monotonic() + READY_TIMEOUT
    last_status: int | None = None
    last_body = b""
    last_error: str | None = None
    attempts = 0

    log(f"[..] {step} (BASE_URL={BASE_URL}, 超时 {READY_TIMEOUT:g}s)")
    while time.monotonic() < deadline:
        attempts += 1

        if is_local_base_url():
            state, health, exit_code = docker_backend_state()
            if state == "absent":
                fail(step, "backend 容器不存在，请先执行 "
                           "`docker compose up -d --build backend`")
            if state in STOPPED_STATES:
                detail = f"backend 容器已停止 (state={state}"
                if exit_code is not None:
                    detail += f", exit_code={exit_code}"
                detail += ")"
                fail(step, detail)
            if state == "running" and health == "healthy":
                log(f"[ok] docker 报告 backend 健康 (health=healthy)")
                return

        status, body, error = http_get(HEALTH_PATH)
        last_status, last_body, last_error = status, body, error
        if error is None and 200 <= (status or 0) < 300:
            try:
                data = json.loads(body.decode("utf-8", "replace"))
                if isinstance(data, dict) and data.get("ok") is True:
                    log(f"[ok] {step}（第 {attempts} 次探测通过）")
                    return
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

        time.sleep(READY_INTERVAL)

    fail(step, f"在 {READY_TIMEOUT:g}s 内未通过健康探测（共探测 {attempts} 次）",
         last_status, last_body, last_error)


def main() -> int:
    wait_until_ready()

    passed = 0
    for name, path, check in STEPS:
        status, body, error = http_get(path)
        data = parse_json(name, status, body, error)
        detail = check(data, status, body)
        passed += 1
        log(f"[PASS] {passed}/3 {name}: HTTP {status} — {detail}")

    log(f"[DONE] API 冒烟全部通过 ({passed}/3)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # 输出被下游管道（如 head）关闭，正常退出
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
        sys.exit(0)
