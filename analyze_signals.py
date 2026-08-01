#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""要因分析（精度向上のエンジン）

data/tracking.json のシグナル(判定時の各指標＋固定ホライズンの将来リターン)から:
  - ホライズン別(1週/1ヶ月/3ヶ月/6ヶ月)の成績（勝率・平均超過リターン）
  - どの指標(PBR/PER/配当/ROE/52週位置/D/E/割安スコア)が超過リターンを当てているか
    = 情報係数(順位相関 Spearman)。標本が足りない項目は「保留」
  - 警告フラグ(バリュートラップ)の有効性
  - 割安スコアの分位別リターン
  - データドリブンな「改善候補」(標本数ガード付き)
を data/analysis.json に出力。

目標指標 = 市場(日経平均)に対する超過リターン(excess)。単一期間・小標本は正直に明示する。
"""
import json
import statistics
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIN_N = 25  # これ未満は「標本不足で保留」
# (キー, 表示名, 期待方向 +1=高いほど良い / -1=低いほど良い)
FACTORS = [("f_pbr", "PBR", -1), ("f_per", "PER", -1), ("f_yield", "配当利回り", +1),
           ("f_roe", "ROE", +1), ("f_pos52", "52週位置", -1), ("f_d2e", "D/E", -1),
           ("entry_score", "割安スコア", +1)]
HORIZONS = [("1m", "1ヶ月"), ("3m", "3ヶ月"), ("6m", "6ヶ月")]


def spearman(xs, ys):
    n = len(xs)
    if n < 5:
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 3) if den else None


def target_excess(sig, horizon):
    """指定ホライズンの超過リターン。無ければNone。"""
    if horizon == "since":
        return sig.get("excess_pct")
    h = (sig.get("horizons") or {}).get(horizon) or {}
    return h.get("exc")


def target_return(sig, horizon):
    if horizon == "since":
        return sig.get("return_pct")
    h = (sig.get("horizons") or {}).get(horizon) or {}
    return h.get("ret")


def agg_excess(sigs, horizon):
    exc = [target_excess(s, horizon) for s in sigs]
    exc = [e for e in exc if e is not None]
    ret = [target_return(s, horizon) for s in sigs]
    ret = [r for r in ret if r is not None]
    if not exc:
        return {"n": 0}
    return {"n": len(exc),
            "win_rate_vs_mkt": round(100 * sum(1 for e in exc if e > 0) / len(exc), 1),
            "avg_excess": round(statistics.mean(exc), 2),
            "median_excess": round(statistics.median(exc), 2),
            "avg_return": round(statistics.mean(ret), 2) if ret else None}


def main():
    tp = ROOT / "data" / "tracking.json"
    if not tp.exists():
        print("tracking.json が無い。先に tracker.py を実行してください")
        return
    sigs = json.loads(tp.read_text(encoding="utf-8")).get("signals", [])
    # 分割疑い(未調整)は除外
    sigs = [s for s in sigs if not s.get("split_suspect")]

    # ホライズン別サマリー
    horizon_summ = []
    for hk, hl in HORIZONS:
        a = agg_excess(sigs, hk)
        a["horizon"] = hl
        a["key"] = hk
        horizon_summ.append(a)
    # 主ホライズン = 標本が MIN_N 以上ある中で最も長いもの（無ければ最も標本が多いもの）
    matured = [h for h in horizon_summ if h["n"] >= MIN_N]
    if matured:
        primary = matured[-1]  # 長い方
    else:
        primary = max(horizon_summ, key=lambda h: h["n"])
    pk = primary["key"]
    confidence = "medium" if primary["n"] >= 100 else ("low" if primary["n"] >= MIN_N else "very_low")

    # 要因分析（情報係数）: 各指標 と 主ホライズンの超過リターン の順位相関
    factor_ic = []
    for key, label, direction in FACTORS:
        xs, ys = [], []
        for s in sigs:
            fv = s.get(key)
            tv = target_excess(s, pk)
            if fv is not None and tv is not None:
                xs.append(fv)
                ys.append(tv)
        ic = spearman(xs, ys)
        # 期待方向で符号を揃えた「効き」= signed_ic（正なら期待どおり効いている）
        signed = round(ic * direction, 3) if ic is not None else None
        factor_ic.append({"key": key, "label": label, "direction": direction,
                          "n": len(xs), "ic": ic, "signed_ic": signed,
                          "reliable": len(xs) >= MIN_N})

    # 警告フラグ(バリュートラップ)の有効性
    flagged = [s for s in sigs if s.get("entry_flags")]
    clean = [s for s in sigs if not s.get("entry_flags")]
    flag_efficacy = {
        "flagged": agg_excess(flagged, pk),
        "clean": agg_excess(clean, pk),
    }

    # 割安スコア分位別（5分位）
    scored = [s for s in sigs if s.get("entry_score") is not None and target_excess(s, pk) is not None]
    quintiles = []
    if len(scored) >= MIN_N:
        scored.sort(key=lambda s: s["entry_score"])
        n = len(scored)
        for q in range(5):
            part = scored[q * n // 5:(q + 1) * n // 5]
            exc = [target_excess(s, pk) for s in part]
            quintiles.append({"q": q + 1,
                              "score_range": f"{part[0]['entry_score']}〜{part[-1]['entry_score']}" if part else "",
                              "n": len(part),
                              "avg_excess": round(statistics.mean(exc), 2) if exc else None})

    # データドリブンな改善候補（標本ガード付き・断定しない）
    findings = []
    for f in factor_ic:
        if not f["reliable"]:
            continue
        s = f["signed_ic"]
        if s is None:
            continue
        if s <= -0.08:
            findings.append(f"⚠️ {f['label']} は期待と逆に効いている兆候（signed IC {s}, n={f['n']}）→ 配点の見直し候補。※要・前向き検証")
        elif s >= 0.08:
            findings.append(f"✅ {f['label']} は効いている（signed IC {s}, n={f['n']}）→ 現状維持〜強化候補")
    fe_f, fe_c = flag_efficacy["flagged"], flag_efficacy["clean"]
    if fe_f.get("n", 0) >= MIN_N and fe_c.get("n", 0) >= MIN_N:
        if fe_f["avg_excess"] < fe_c["avg_excess"] - 1:
            findings.append(f"✅ 警告フラグは機能: フラグ付き平均超過{fe_f['avg_excess']}% < クリーン{fe_c['avg_excess']}%（トラップ回避に有効）")
        elif fe_f["avg_excess"] > fe_c["avg_excess"] + 1:
            findings.append(f"⚠️ 警告フラグが逆効果の兆候: フラグ付き{fe_f['avg_excess']}% > クリーン{fe_c['avg_excess']}% → フラグ条件の再検討候補")
    if not findings:
        findings.append("現時点は標本不足で確度の高い改善候補なし。データ蓄積を優先。")

    caveats = [
        f"母集団のスナップショット数={len(json.loads(tp.read_text(encoding='utf-8')).get('signals',[])) and ''}".strip() or "",
        f"主ホライズン={dict(HORIZONS).get(pk, pk)}・標本n={primary['n']}・信頼度={confidence}",
        "単一期間の結果。相場局面（バリュー優位/劣位）で大きく変わる。複数期間の蓄積が必要。",
        "リターンは分割・配当調整済み。目標=対日経の超過リターン。",
        "改善は必ず『新しいデータで前向きに』検証してから反映（過去最適化＝カーブフィッティング回避）。",
    ]
    caveats = [c for c in caveats if c]

    out = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "primary_horizon": {"key": pk, "label": dict(HORIZONS).get(pk, pk), "n": primary["n"]},
        "confidence": confidence,
        "target": "対日経の超過リターン(excess)",
        "horizon_summary": horizon_summ,
        "factor_ic": factor_ic,
        "flag_efficacy": flag_efficacy,
        "score_quintiles": quintiles,
        "findings": findings,
        "caveats": caveats,
    }
    (ROOT / "data" / "analysis.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    print(f"analysis.json 生成: 主ホライズン={out['primary_horizon']['label']}(n={primary['n']}, 信頼度{confidence})")
    for f in sorted([x for x in factor_ic if x['reliable']], key=lambda x: -(x['signed_ic'] or -9)):
        print(f"  {f['label']:8s} signed_IC={f['signed_ic']}  (IC={f['ic']}, n={f['n']})")
    for fnd in findings:
        print("  ・" + fnd)


if __name__ == "__main__":
    main()
