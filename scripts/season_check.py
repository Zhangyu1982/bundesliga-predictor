"""赛季边界三方对账：赛程 vs 本季名单 vs 参数库。任一不等就落地一份报告。"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.season_roster import load_roster                     # noqa: E402
from data.param_status import code_to_eng                      # noqa: E402


def reconcile(round_codes, roster, param_codes, provisional_codes=None) -> dict:
    """返回对账结论 + 需要人看的问题清单。"""
    round_codes = set(round_codes or [])
    roster = set(roster or [])
    param_codes = set(param_codes or [])
    provisional_codes = set(provisional_codes or [])

    outside = sorted(round_codes - roster)          # 赛程里出现名单外球队 = 跑错批次/串季
    absent = sorted(roster - round_codes)           # 本季球队没出现在赛程里
    no_param = sorted(roster - param_codes)         # 名单内但模型没参数
    only_prov = sorted((roster & param_codes) & provisional_codes)
    problems = []
    if outside:
        problems.append("名单外球队：" + "/".join(outside))
    if absent:
        problems.append("名单内未出场：" + "/".join(absent))
    if no_param:
        problems.append("无模型参数：" + "/".join(no_param))
    return {
        "ok": not problems,
        "roster_size": len(roster),
        "round_size": len(round_codes),
        "outside": outside,
        "absent": absent,
        "no_param": no_param,
        "provisional": only_prov,
        "problems": problems,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default=None)
    ap.add_argument("--round", type=int, default=None)
    ap.add_argument("--out", default="data/season_transition_report.json")
    a = ap.parse_args()

    from data.timeutil import season_label
    season = a.season or season_label()
    roster = load_roster(season)
    if roster is None:
        print(f"[FAIL] 本季名单未登记（season={season}）")
        return 1

    from pipeline import PredictionPipeline
    p = PredictionPipeline()
    fx, _meta = (p.fetch_round(a.round) if a.round else ([], {}))
    if a.round and not fx:
        print("[WARN] 赛程未取到（配额或源不可用），只对账名单与参数")
        round_codes = set()
    else:
        round_codes = {c for f in fx for c in (f.get("home_code"), f.get("away_code")) if c} or set()
    if not round_codes:
        round_codes = set(roster)    # 无赛程时退化为"整季名单"，只核对参数覆盖

    from data.historical_data import CODE2ENG
    _eng2code = {v: k for k, v in CODE2ENG.items()}
    param_codes = {_eng2code[e] for e in (p._bundle.dc_attack or {}) if e in _eng2code}
    prov_codes = {_eng2code.get(t, t) for t in (getattr(p._bundle, "provisional", []) or [])}
    res = reconcile(round_codes, roster, param_codes, prov_codes)
    res.update({"season": season, "round": a.round})
    try:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    print(f"赛季 {season} 对账：{'通过' if res['ok'] else '有问题'}")
    for x in res["problems"]:
        print("  -", x)
    print("  临时参数球队：", "/".join(code_to_eng(c) for c in res["provisional"]) or "无")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
