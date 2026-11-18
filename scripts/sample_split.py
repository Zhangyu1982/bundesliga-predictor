"""统计层口径切分 CLI：全样本 / 仅稳定参数 / 含临时参数 / 含衰减参数。

只切分、不剔除：每一场都留在全样本里，另外给出更可比的口径。
用法：python3 scripts/sample_split.py --season 2627 --min-n 10
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.sample_split import report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default=None, help="赛季标签，如 2627；缺省取最新记录的赛季")
    ap.add_argument("--min-n", type=int, default=10, help="样本低于该值只标注、不下结论")
    ap.add_argument("--out", default=os.path.join("data", "sample_split_report.json"))
    a = ap.parse_args()
    res = report(season=a.season, min_n=a.min_n)
    print(f"赛季 {res['season']} 口径切分（min_n={a.min_n}）")
    for k in ("all", "stable", "provisional", "carryover"):
        s = res["stats"][k]
        flag = "（样本不足，仅参考）" if s["insufficient"] else ""
        print(f"  {k:<12} n={s['n']:<3} 命中={s['hits']:<3} 命中率={s['hit_rate']:.1%}{flag}")
    print(f"  临时参数队：{'/'.join(res['provisional_teams']) or '—'}")
    print(f"  衰减回归队：{'/'.join(res['carryover_teams']) or '—'}")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
