import json, urllib.request
from datetime import datetime, timezone, timedelta
from scripts.round_checklist import check_round, all_pass
from pipeline import PredictionPipeline, fixture_fingerprint
from data.api_client import team_zh
from data.timeutil import utc_now_iso

CST = timezone(timedelta(hours=8))
NAME2CODE = {"FC Cologne":"KOE","Bayern Munich":"BAY","1. FC Union Berlin":"FCU","RB Leipzig":"RBL",
 "Bayer Leverkusen":"LEV","SC Paderborn 07":"PAD","Borussia Mönchengladbach":"BMG","Borussia Dortmund":"DOR",
 "TSG Hoffenheim":"TSG","Eintracht Frankfurt":"SGE","SC Freiburg":"SCF","SV Elversberg":"ELV",
 "VfB Stuttgart":"STU","Schalke 04":"SCH","Mainz":"MAI","FC Augsburg":"FCA","Hamburg SV":"HSV","Werder Bremen":"SVW"}
fx=[]
for d in ("20261120","20261121","20261122"):
    u=f"https://site.api.espn.com/apis/site/v2/sports/soccer/ger.1/scoreboard?dates={d}"
    req=urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"})
    data=json.load(urllib.request.urlopen(req, timeout=30))
    for e in data.get("events") or []:
        c=e["competitions"][0]; t=[x["team"]["displayName"] for x in c["competitors"]]
        home,away=(t[0],t[1]) if c["competitors"][0]["homeAway"]=="home" else (t[1],t[0])
        fx.append({"match_id":e["id"],"home_code":NAME2CODE[home],"away_code":NAME2CODE[away],
                   "home_team":team_zh(NAME2CODE[home]),"away_team":team_zh(NAME2CODE[away]),
                   "kickoff":e["date"],"status":"TIMED","matchday":10})
fetched=utc_now_iso()
meta={"freshness":{"source":"ESPN","cached":False,"fetched_at":fetched},
      "fixtures_source":"live_espn","fixture_snapshot":fixture_fingerprint(fx, fetched_at=fetched)}
p=PredictionPipeline()
cover={c for c,eng in __import__("data.historical_data",fromlist=["CODE2ENG"]).CODE2ENG.items() if eng in p._bundle.dc_attack}
for w in (("2026-11-21","2026-11-22"),("2026-11-20","2026-11-22")):
    print(f"--- 窗口 {w[0]} ~ {w[1]}")
    for r in check_round(fx, meta, cover, w):
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['no']}. {r['detail']}")
    print("  结论:", "可出卡" if all_pass(check_round(fx,meta,cover,w)) else "拒绝出卡")
