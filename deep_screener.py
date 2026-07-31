#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
決算深掘りスクリーナー（質×割安）
- latest.json の割安候補（スコア55+）について、複数年の決算を読み込み
- 利益の継続性・成長・営業CF・利益の質・配当の持続性・ネットキャッシュを評価
- 「安いだけ」のバリュートラップを除外し、Sランク厳選リストを生成
出力: data/deep.json
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

CAND_MIN = 55   # 深掘り対象にする割安スコアの下限


def fnum(v):
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def row(df, *keys):
    """財務DataFrameから行ラベル部分一致でSeries（新しい年→古い年）を返す"""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        cols = list(df.columns)  # 日付（新しい順のことが多い）
        cols_sorted = sorted(cols, reverse=True)
        for idx in df.index:
            s = str(idx)
            if all(k in s for k in keys[0]) if isinstance(keys[0], tuple) else any(k in s for k in keys):
                vals = []
                for c in cols_sorted:
                    vals.append(fnum(df.loc[idx, c]))
                return vals
    except Exception:
        return []
    return []


def series_by(df, label):
    if df is None or getattr(df, "empty", True):
        return []
    try:
        cols_sorted = sorted(list(df.columns), reverse=True)
        for idx in df.index:
            if label in str(idx):
                return [fnum(df.loc[idx, c]) for c in cols_sorted]
    except Exception:
        pass
    return []


def consec_positive(vals):
    n = 0
    for v in vals:
        if v is not None and v > 0:
            n += 1
        else:
            break
    return n


def consec_growth(vals):
    """新しい→古い順のvalsで、直近から連続して前年より増えている年数"""
    n = 0
    for i in range(len(vals) - 1):
        a, b = vals[i], vals[i + 1]
        if a is not None and b is not None and b != 0 and a > b:
            n += 1
        else:
            break
    return n


def analyze(stock):
    import yfinance as yf
    code = stock["code"]
    for attempt in range(3):
        try:
            t = yf.Ticker(f"{code}.T")
            info = t.info or {}
            fin = t.financials
            cf = t.cashflow
            bs = t.balance_sheet
            break
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(3 * (attempt + 1) + (8 if "429" in str(e) else 0))
    else:
        return None

    rev = series_by(fin, "Total Revenue") or series_by(fin, "Operating Revenue")
    op = series_by(fin, "Operating Income")
    ni = series_by(fin, "Net Income")
    ocf = series_by(cf, "Operating Cash Flow")
    fcf = series_by(cf, "Free Cash Flow")

    # info由来の質指標
    op_m = fnum(info.get("operatingMargins"))
    net_m = fnum(info.get("profitMargins"))
    payout = fnum(info.get("payoutRatio"))
    roa = fnum(info.get("returnOnAssets"))
    cur = fnum(info.get("currentRatio"))
    cash = fnum(info.get("totalCash"))
    debt = fnum(info.get("totalDebt"))
    mcap = fnum(info.get("marketCap"))
    eg = fnum(info.get("earningsGrowth"))
    qeg = fnum(info.get("earningsQuarterlyGrowth"))
    rg = fnum(info.get("revenueGrowth"))
    d2e = fnum(info.get("debtToEquity"))
    fwd = fnum(info.get("forwardPE"))   # 来期予想PER（ピーク益/一過性益の検出に使う）

    for x in ("op_m", "net_m", "payout", "roa"):
        v = locals()[x]
    op_m = op_m * 100 if op_m is not None and abs(op_m) <= 3 else op_m
    net_m = net_m * 100 if net_m is not None and abs(net_m) <= 3 else net_m
    roa = roa * 100 if roa is not None and abs(roa) <= 3 else roa
    payout_pct = payout * 100 if payout is not None and payout <= 3 else payout
    eg_pct = eg * 100 if eg is not None and abs(eg) <= 10 else eg
    qeg_pct = qeg * 100 if qeg is not None and abs(qeg) <= 10 else qeg
    rg_pct = rg * 100 if rg is not None and abs(rg) <= 10 else rg

    # 欠損(None)を除いた系列（新しい→古い順を保持）で判定する
    ni_c = [v for v in ni if v is not None]
    op_c = [v for v in op if v is not None]
    ocf_c = [v for v in ocf if v is not None]
    rev_c = [v for v in rev if v is not None]
    ni_latest = ni_c[0] if ni_c else None
    op_latest = op_c[0] if op_c else None
    ocf_latest = ocf_c[0] if ocf_c else None

    net_cash = (cash - debt) if (cash is not None and debt is not None) else None
    net_cash_ratio = (net_cash / mcap) if (net_cash is not None and mcap) else None
    ocf_margin = (ocf_latest / rev_c[0] * 100) if (ocf_latest is not None and rev_c and rev_c[0]) else None

    cy_profit = consec_positive(ni_c)        # 連続黒字（純利益）
    cy_growth = consec_growth(ni_c)          # 連続増益（純利益）

    # 一過性益フラグ: 直近の純利益が営業利益を大きく上回る or 営業赤字なのに純黒字
    one_off = False
    if ni_latest is not None and op_latest is not None:
        if op_latest <= 0 and ni_latest > 0:
            one_off = True
        elif op_latest > 0 and ni_latest > op_latest * 1.6:
            one_off = True

    # ---- 質スコア(0-40) ----
    q = 0
    qd = {}
    qd["営業利益率"] = 8 if (op_m is not None and op_m >= 10) else 4 if (op_m is not None and op_m >= 6) else 0
    qd["純利益率"] = 5 if (net_m is not None and net_m >= 6) else 2 if (net_m is not None and net_m >= 3) else 0
    qd["連続黒字"] = 6 if cy_profit >= 4 else 4 if cy_profit >= 3 else 0
    qd["増益基調"] = 6 if (cy_growth >= 2 or (eg_pct is not None and eg_pct > 0 and rg_pct is not None and rg_pct > 0)) else 0
    qd["営業CF"] = 5 if (ocf_latest is not None and ocf_latest > 0) else 0
    qd["ネットキャッシュ"] = 6 if (net_cash_ratio is not None and net_cash_ratio >= 0.3) else 3 if (net_cash_ratio is not None and net_cash_ratio >= 0.1) else 0
    qd["ROA"] = 4 if (roa is not None and roa >= 5) else 2 if (roa is not None and roa >= 3) else 0
    q = sum(qd.values())

    # ---- ハードゲート（Sランクの条件） ----
    base = stock["score"]
    per = stock.get("per")
    fwd_ratio = (fwd / per) if (fwd is not None and per is not None and per > 0 and fwd > 0) else None

    gates = []
    if not (ni_latest is not None and ni_latest > 0):
        gates.append("直近純利益が赤字")
    if op_m is not None and op_m <= 0:
        gates.append("本業が営業赤字")
    if ocf_latest is not None and ocf_latest <= 0:
        gates.append("営業CFがマイナス")
    if payout_pct is not None and payout_pct > 80:
        gates.append(f"配当性向過大({payout_pct:.0f}%)")
    if qeg_pct is not None and qeg_pct < -25:
        gates.append(f"四半期利益急減({qeg_pct:.0f}%)")
    if d2e is not None and d2e > 200:
        gates.append(f"過剰債務(D/E{d2e:.0f}%)")
    if one_off:
        gates.append("一過性利益の疑い")
    if cy_profit < 3:
        gates.append(f"黒字継続{cy_profit}年")
    # 来期予想PERが今より大幅に高い＝来期減益見込み＝今期がピーク益/一過性益
    if fwd_ratio is not None and fwd_ratio > 1.4:
        gates.append(f"来期減益見込み(予想PER{fwd:.0f})")

    # 増益基調か（連続増益 or 増収増益）
    grows = (cy_growth >= 1) or (eg_pct is not None and eg_pct > 0 and rg_pct is not None and rg_pct > 0)
    # 本当に割安か（資産or収益のどちらかで明確に割安。PER/配当/成長だけで紛れ込むのを防ぐ）
    cheap = (stock.get("pbr") is not None and stock["pbr"] < 1.2) or (per is not None and 0 < per < 10)

    total = base + q
    if (not gates) and q >= 22 and base >= 62 and grows and (op_m is not None and op_m >= 5) and cheap:
        tier = "S+"          # 割安×決算良好×増益×本業しっかり×警告ゼロ
    elif len(gates) <= 1 and q >= 16 and base >= 58 and (op_m is None or op_m > 0):
        tier = "S"
    elif base >= 60:
        tier = "A"
    else:
        tier = "B"

    return {
        **{k: stock.get(k) for k in ("code", "name", "sector", "price", "per", "pbr",
                                     "yield", "roe", "score")},
        "base": base, "quality": q, "qparts": qd, "total": total, "tier": tier,
        "gates": gates, "one_off": one_off, "forward_pe": fwd, "fwd_ratio": fwd_ratio, "grows": grows,
        "op_margin": op_m, "net_margin": net_m, "roa": roa, "payout": payout_pct,
        "current": cur, "d2e": d2e,
        "net_cash": net_cash, "net_cash_ratio": net_cash_ratio, "ocf_margin": ocf_margin,
        "consec_profit": cy_profit, "consec_growth": cy_growth,
        "rev_growth": rg_pct, "eps_growth": eg_pct, "q_growth": qeg_pct,
        "ni_series": ni[:5], "op_series": op[:5], "rev_series": rev[:5],
    }


def main():
    latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    cands = [r for r in latest["results"] if r["score"] >= CAND_MIN]
    print(f"深掘り対象: {len(cands)}銘柄（割安スコア{CAND_MIN}+）")

    out, fail = [], 0
    done = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(analyze, c): c for c in cands}
        for fut in as_completed(futs):
            done += 1
            r = fut.result()
            if r:
                out.append(r)
            else:
                fail += 1
            if done % 50 == 0:
                print(f"  {done}/{len(cands)} 完了（失敗{fail}）", flush=True)

    rank = {"S+": 0, "S": 1, "A": 2, "B": 3}
    out.sort(key=lambda x: (rank[x["tier"]], -x["total"]))

    payload = {
        "date": latest["date"],
        "generated_at": latest["generated_at"],
        "candidates": len(cands), "analyzed": len(out), "failed": fail,
        "results": out,
    }
    (DATA / "deep.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    sp = [r for r in out if r["tier"] == "S+"]
    s = [r for r in out if r["tier"] == "S"]
    print(f"\n完了: {len(out)}銘柄分析（失敗{fail}）")
    print(f"S+ランク（最厳選）: {len(sp)} ／ Sランク: {len(s)}")
    def nf(v, d=1):
        return "—" if v is None else f"{v:.{d}f}"
    print("\n=== S+ランク（質×割安・決算良好） ===")
    for r in sp:
        print(f"{r['code']} {r['name'][:14]:14s} 総合{r['total']}(割安{r['base']}+質{r['quality']}) "
              f"PBR{nf(r['pbr'],2)} PER{nf(r['per'])} 配当{nf(r['yield'])}% "
              f"連続黒字{r['consec_profit']}年 営業益率{nf(r['op_margin'])}% "
              f"純現金/時価{nf((r['net_cash_ratio'] or 0)*100,0)}%")


if __name__ == "__main__":
    main()
