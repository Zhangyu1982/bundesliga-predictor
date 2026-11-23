"""强制入口：仓库里只允许一处代码直接访问 raw.githubusercontent（同步实现本身）。"""
import os
import re

RAW = "raw." + "githubusercontent.com"
ALLOW = {"scripts/sync_from_raw.ps1"}
SKIP_DIRS = {".git", ".venv", "logs", "__pycache__", ".pytest_cache"}


def _iter_code(root="."):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith((".py", ".ps1")):
                yield os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")


def test_only_sync_script_talks_to_raw():
    offenders = []
    me = "tests/test_sync_guard.py"
    for rel in _iter_code():
        if rel in ALLOW or rel == me:
            continue
        with open(rel, encoding="utf-8", errors="ignore") as f:
            if RAW in f.read():
                offenders.append(rel)
    assert not offenders, "禁止绕过唯一同步入口：{}".format(offenders)


def test_sync_script_keeps_size_checks():
    path = "scripts/sync_from_raw.ps1"
    text = open(path, encoding="utf-8").read()
    assert "-f" in text and "$LASTEXITCODE" in text, "必须用 curl 退出码判定失败"
    assert ".Length -gt 0" in text, "必须校验非空（0 字节文件比缺文件更危险）"
    assert "retry" in text, "必须有重试"


def test_root_entry_exists_and_delegates():
    text = open("sync_from_raw.ps1", encoding="utf-8").read()
    assert "scripts\\sync_from_raw.ps1" in text or "scripts/sync_from_raw.ps1" in text
