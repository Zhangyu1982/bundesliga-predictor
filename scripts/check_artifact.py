"""产物一致性检查 CLI：打印本机 models.pkl 的参数指纹，可与另一环境比对。

用法：python3 scripts/check_artifact.py [--expect <16位指纹>]
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.artifact_check import check


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", default=None, help="期望的 16 位参数指纹")
    a = ap.parse_args()
    ok, s = check(expect=a.expect)
    print(f"产物：teams={s['teams']} 核心均值 attack={s['core_mean_attack']} "
          f"临时参数队={len(s['provisional'])}")
    print(f"  mu={s['mu']} rho={s['rho']} elo={s['elo']} metrics={s['metrics']}")
    print(f"  参数指纹：{s['fingerprint']}")
    if a.expect:
        print("  一致性：" + ("一致" if ok else f"不一致（期望 {a.expect}）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
