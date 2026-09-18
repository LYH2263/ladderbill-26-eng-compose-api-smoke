#!/usr/bin/env python3
"""Ladderbill API 冒烟检查。

待 backend 健康探针就绪后，依次请求三个端点并校验 HTTP 2xx 与 JSON 必要字段：
  1. GET /api/health    —— 健康探针（字段 ok 为 true）
  2. GET /api/accounts  —— 户列表（items 非空，元素含 id/name/meter_no）
  3. GET /api/tiers     —— 默认档表列表（items 非空，元素含 id/up_to/price/sort_order，up_to 允许为 null）

任一步失败：打印失败步骤名与响应摘要，以退出码 1 结束；全部通过以退出码 0 结束。
只发 GET 请求，幂等，可在干净 shell 重复执行；仅依赖 Python 3 标准库，不依赖前端构建产物。

环境变量：
  BASE_URL            后端基础地址，默认 http://localhost:9100（对应 compose 的 9100:9100 映射）
  SMOKE_WAIT_SECONDS  等待健康探针就绪的上限秒数，默认 10（backend 停止时在此上限内快速失败）
  SMOKE_REQ_TIMEOUT   单次请求超时秒数，默认 3
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:9100").rstrip("/")
WAIT_SECONDS = float(os.environ.get("SMOKE_WAIT_SECONDS", "10"))
REQ_TIMEOUT = float(os.environ.get("SMOKE_REQ_TIMEOUT", "3"))
POLL_INTERVAL = 0.5
SUMMARY_LEN = 300


def fetch(path):
    """GET BASE_URL+path，返回 (status, body, conn_error)。status 为 None 表示连接失败。"""
    req = urllib.request.Request(BASE_URL + path, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:  # 服务器有响应但非 2xx/3xx
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, None
    except Exception as e:  # 连接拒绝/超时等：后端未就绪或已停止
        return None, "", f"{type(e).__name__}: {e}"


def summarize(body):
    """响应摘要：压缩空白并截断，避免刷屏。"""
    text = " ".join(body.split())
    return text[:SUMMARY_LEN] + ("..." if len(text) > SUMMARY_LEN else "") or "(empty body)"


def check_health(data):
    if not isinstance(data, dict):
        return "body is not a JSON object"
    if "ok" not in data:
        return "missing field: ok"
    if data["ok"] is not True:
        return f"field ok is not true (got {data['ok']!r})"
    return None


def make_list_validator(required_fields, nullable_fields=()):
    def validate(data):
        if not isinstance(data, dict):
            return "body is not a JSON object"
        items = data.get("items")
        if not isinstance(items, list):
            return "missing or non-list field: items"
        if not items:
            return "items is empty (seed data expected)"
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                return f"items[{i}] is not an object"
            for field in required_fields:
                if field not in item:
                    return f"items[{i}] missing field: {field}"
                if item[field] is None and field not in nullable_fields:
                    return f"items[{i}] field {field} is null"
        return None

    return validate


STEPS = [
    ("health", "/api/health", check_health),
    ("accounts(户列表)", "/api/accounts", make_list_validator(["id", "name", "meter_no"])),
    (
        "tiers(默认档表)",
        "/api/tiers",
        make_list_validator(["id", "up_to", "price", "sort_order"], nullable_fields=("up_to",)),
    ),
]


def run_step(name, path, validate, retries):
    """执行一步；retries 为 True 时在等待上限内轮询（用于健康探针就绪）。返回错误描述或 None。"""
    deadline = time.monotonic() + (WAIT_SECONDS if retries else 0)
    while True:
        status, body, conn_err = fetch(path)
        if conn_err is not None:
            err = f"connection failed: {conn_err}"
        elif not (200 <= status < 300):
            err = f"HTTP {status} (expected 2xx); body: {summarize(body)}"
        else:
            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                err = f"invalid JSON: {e}; body: {summarize(body)}"
            else:
                field_err = validate(data)
                err = f"{field_err}; body: {summarize(body)}" if field_err else None
        if err is None or time.monotonic() >= deadline:
            return err
        time.sleep(POLL_INTERVAL)


def main():
    print(f"smoke target: {BASE_URL} (health wait <= {WAIT_SECONDS:g}s, req timeout {REQ_TIMEOUT:g}s)")
    for i, (name, path, validate) in enumerate(STEPS, 1):
        # 第 1 步即健康探针：在等待上限内轮询就绪；后续步骤服务已就绪，单次请求即可
        err = run_step(name, path, validate, retries=(i == 1))
        if err is not None:
            print(f"[{i}/{len(STEPS)}] {name} GET {path} ... FAIL")
            print(f"  {err}")
            print(f"SMOKE FAIL at step '{name}'", file=sys.stderr)
            return 1
        print(f"[{i}/{len(STEPS)}] {name} GET {path} ... OK")
    print(f"SMOKE PASS: {len(STEPS)}/{len(STEPS)} steps succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
