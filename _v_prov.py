from pipeline import PredictionPipeline
p = PredictionPipeline()
b = p._bundle
print("provisional:", getattr(b, "provisional", None))
print("dc覆盖队数:", len(b.dc_attack))
fx, meta = p.fetch_round(10)
print("fixtures_source:", meta.get("fixtures_source"), "场次:", len(fx))
res = p.predict_round(10, stage="初版")
print("cards:", len(res.get("cards") or []), "coverage_error:", res.get("coverage_error"))
for c in (res.get("cards") or [])[:4]:
    print(f"  {c.get('home_zh','')} vs {c.get('away_zh','')} | 临时队={c.get('provisional_teams')} | λ={c.get('lam_home')}/{c.get('lam_away')}")
