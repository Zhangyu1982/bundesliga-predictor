"""产物一致性检查：给出 models.pkl 的参数指纹，可跨环境比对。

背景：XGBoost booster 绑本机二进制，pkl 不能跨平台复制；但**参数本身应当一致**。
用同一份历史数据在两台机器各自重训后，DC 参数与 Elo 应按位相同，指纹也应相同。
"""
import hashlib
import json
import os
import pickle

_ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "artifacts", "models.pkl")


def load_bundle(path: str | None = None):
    with open(path or _ART, "rb") as f:
        return pickle.load(f)


def fingerprint(bundle) -> str:
    """DC 攻/防（6 位）+ Elo（3 位）的排序哈希。刻意不含 GBM 权重（跨框架不可比）。"""
    att = {k: round(float(v), 6) for k, v in (bundle.dc_attack or {}).items()}
    dfn = {k: round(float(v), 6) for k, v in (bundle.dc_defence or {}).items()}
    elo = {k: round(float(v), 3) for k, v in (getattr(bundle, "elo_ratings", {}) or {}).items()}
    payload = json.dumps([sorted(att.items()), sorted(dfn.items()), sorted(elo.items())],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def summary(bundle) -> dict:
    att = bundle.dc_attack or {}
    prov = list(getattr(bundle, "provisional", []) or [])
    core = [v for k, v in att.items() if k not in set(prov)]
    return {
        "teams": len(att),
        "core_mean_attack": round(sum(core) / len(core), 4) if core else 0.0,
        "provisional": sorted(prov),
        "mu": [round(bundle.dc_mu_home, 6), round(bundle.dc_mu_away, 6)],
        "rho": bundle.dc_rho,
        "elo": [bundle.elo_k, bundle.elo_home_adv],
        "metrics": {k: round(float(v), 6) for k, v in (bundle.metrics or {}).items()},
        "fingerprint": fingerprint(bundle),
    }


def check(expect: str | None = None, path: str | None = None) -> tuple[bool, dict]:
    s = summary(load_bundle(path))
    ok = (expect is None) or (s["fingerprint"] == expect)
    return ok, s
