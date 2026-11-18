"""用 ESPN 真实第10轮赛程跑一遍 predict_round（不依赖 football-data 配额）。"""
from pipeline import PredictionPipeline
from data.api_client import team_zh
from data.timeutil import utc_now_iso

PAIRS = [("BAY","KOE","2026-11-20T19:30Z"), ("FCU","RBL","2026-11-21T14:30Z"),
         ("LEV","PAD","2026-11-21T14:30Z"), ("BMG","DOR","2026-11-21T14:30Z"),
         ("SGE","TSG","2026-11-21T14:30Z"), ("ELV","SCF","2026-11-21T14:30Z"),
         ("SCH","STU","2026-11-21T17:30Z"), ("FCA","MAI","2026-11-22T14:30Z"),
         ("SVW","HSV","2026-11-22T16:30Z")]
fx = [{"match_id": f"espn{i}", "competition": "BL1", "home_code": h, "away_code": a,
       "home_team": team_zh(h), "away_team": team_zh(a), "kickoff": k, "status": "TIMED",
       "matchday": 10, "score": {}, "stage": "初版", "last_updated": utc_now_iso(),
       "standings": {}, "form_last3": {"home": [], "away": []},
       "injuries": {"home_key_out": [], "away_key_out": [], "home_doubtful": [], "away_doubtful": []},
       "confidence": "中"} for i,(h,a,k) in enumerate(PAIRS)]
meta = {"freshness": {"source": "espn", "cached": False, "fetched_at": utc_now_iso()},
        "realtime_injury_available": False, "round_has_data": True, "round_no": 10,
        "fixtures_source": "live_espn"}
p = PredictionPipeline()
p.fetch_round = lambda r: (fx, dict(meta))
res = p.predict_round(10, stage="初版")
print("coverage_error:", res.get("coverage_error"))
print("cards:", len(res["cards"]))
for c in res["cards"]:
    lm = (c.get("lam") or {}).get("total")
    print(f"  {c['home_team']:6s} vs {c['away_team']:6s} | 推荐 {c['pick']:8s} | λ合计 {lm} | 临时 {c['provisional_teams']}")
