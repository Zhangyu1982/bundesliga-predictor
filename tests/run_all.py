"""无依赖测试 runner（venv 未装 pytest）。
运行：python tests/run_all.py
扫描 tests/test_*.py 里的 test_* 函数逐个执行，汇总通过/失败，失败时打印 traceback。
"""
import importlib.util
import inspect
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TESTS = os.path.join(ROOT, "tests")

passed = failed = 0
failures = []
for fn in sorted(os.listdir(TESTS)):
    if not (fn.startswith("test_") and fn.endswith(".py")):
        continue
    path = os.path.join(TESTS, fn)
    spec = importlib.util.spec_from_file_location(f"t_{fn[:-3]}", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        failed += 1
        failures.append((f"{fn} (import)", traceback.format_exc()))
        continue
    for name, obj in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_") or obj.__module__ != mod.__name__:
            continue
        try:
            obj()
            passed += 1
        except Exception:
            failed += 1
            failures.append((f"{fn}::{name}", traceback.format_exc()))

print(f"\n{'='*60}\nPASSED {passed} / FAILED {failed}\n{'='*60}")
for name, tb in failures:
    print(f"\n[FAIL] {name}\n{tb}")
sys.exit(1 if failed else 0)
