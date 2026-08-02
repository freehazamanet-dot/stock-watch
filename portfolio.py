#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""¥100万 ルール運用シミュレーション（ペーパーポートフォリオ）

ルール v1（割安×質）:
- 買い候補: 厳選(score>=strict_min & 警告フラグなし) かつ ROE>=8% を ROE降順（=質のティルト）
- 保有: 10銘柄・ほぼ均等（1枠 = 総資産/10）
- リバランス: 28日以上あけて（≒月次）、ランク外に落ちた保有を売り・新規上位で10枠に補充
- 売り: -25%で暴落ストップ（日次）、最長365日で入替、それ以外はリバランス主導
- コスト: 売買ごとに0.1%（往復0.2%）控除
- 起点: 最古スナップショット(6/13)から遡及シミュ＋以降フォワード。日次で時価評価
- 価格: yfinance の分割・配当調整済（トータルリターン基準）
- ベンチマーク: 同額を日経平均(^N225)に投じて日次評価

注意: 単元(100株)未満の端数は簡略化し「¥枠」で保有（総リターンの推計としては十分）。
S+/S深掘りランクは過去分が保存されていないため、履歴から再現可能な近似(厳選×ROE)を使用。
出力: data/portfolio.json
"""
import json
import glob
import os
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
START_CAPITAL = 1_000_000
N_HOLD = 10
FEE = 0.001          # 片道0.1%（往復0.2%）
DISASTER = -0.25     # 暴落ストップ
MAX_HOLD_DAYS = 365
REBAL_MIN_DAYS = 28
ROE_FLOOR = 8.0


def load_snapshots():
    snaps = []
    for f in sorted(glob.glob(str(ROOT / "history" / "*.json"))):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        date = d.get("date") or os.path.basename(f)[:10]
        bycode = {}
        for r in (d.get("results") or d.get("stocks") or []):
            c = r.get("code")
            if c is not None:
                bycode[str(c)] = r
        snaps.append({"date": date, "strict_min": d.get("strict_min", 70), "bycode": bycode})
    snaps.sort(key=lambda s: s["date"])
    return snaps


def target_list(snap):
    """その日の買い候補（厳選×ROE≥8 を ROE降順）→ コード配列"""
    sm = snap["strict_min"]
    cands = []
    for c, r in snap["bycode"].items():
        sc = r.get("score")
        roe = r.get("roe")
        if sc is None or sc < sm or r.get("flags"):
            continue
        if roe is None or roe < ROE_FLOOR:
            continue
        cands.append((c, roe, sc))
    cands.sort(key=lambda x: (-x[1], -x[2]))
    return [c for c, _, _ in cands]


def fetch_adjusted(codes, start_date):
    try:
        import yfinance as yf
        start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
        end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        df = yf.download([f"{c}.T" for c in codes], start=start, end=end, auto_adjust=True,
                         progress=False, threads=True, group_by="ticker")
        out = {}
        for c in codes:
            t = f"{c}.T"
            try:
                if t not in df.columns.get_level_values(0):
                    continue
                s = {ts.date().isoformat(): float(row["Close"])
                     for ts, row in df[t].iterrows() if row["Close"] == row["Close"]}
                if s:
                    out[c] = s
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"  [warn] 価格取得失敗: {e}")
        return {}


def nikkei_series(start_date):
    try:
        import yfinance as yf
        start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
        end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        h = yf.Ticker("^N225").history(start=start, end=end, auto_adjust=True)
        return {ts.date().isoformat(): float(r["Close"]) for ts, r in h.iterrows()}
    except Exception as e:
        print(f"  [warn] 日経取得失敗: {e}")
        return {}


def px(series, date_str):
    """その日以前で最も近い価格（キャリーフォワード）"""
    if not series:
        return None
    if date_str in series:
        return series[date_str]
    d = date_str
    for _ in range(12):
        d = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
        if d in series:
            return series[d]
    return None


def main():
    snaps = load_snapshots()
    if not snaps:
        print("スナップショットがありません")
        return
    start_date = snaps[0]["date"]

    # リバランス日（28日以上あけて）と各日のターゲット
    rebal = []
    last = None
    for s in snaps:
        if last is None or (datetime.date.fromisoformat(s["date"]) - datetime.date.fromisoformat(last)).days >= REBAL_MIN_DAYS:
            rebal.append(s)
            last = s["date"]
    rebal_by_date = {s["date"]: target_list(s) for s in rebal}
    name_of = {}
    for s in snaps:
        for c, r in s["bycode"].items():
            name_of[c] = r.get("name")

    # 保有しうる全銘柄の価格を取得
    universe = sorted({c for tl in rebal_by_date.values() for c in tl[:N_HOLD * 3]})
    print(f"  価格取得: {len(universe)}銘柄...")
    adj = fetch_adjusted(universe, start_date)
    nk = nikkei_series(start_date)

    # 取引カレンダー = 日経の営業日（起点以降）
    cal = sorted(d for d in nk if d >= start_date)
    if not cal:
        print("カレンダー取得失敗")
        return
    nk_start = px(nk, cal[0])

    cash = START_CAPITAL
    holds = {}   # code -> {name, entry_date, entry_px, invested}
    trades = []
    equity_curve = []
    rebal_dates = set(rebal_by_date)

    def hold_value(c, d):
        p = px(adj.get(c), d)
        if p is None or holds[c]["entry_px"] in (None, 0):
            return holds[c]["invested"]
        return holds[c]["invested"] * (p / holds[c]["entry_px"])

    def total_equity(d):
        return cash + sum(hold_value(c, d) for c in holds)

    def sell(c, d, reason):
        nonlocal cash
        val = hold_value(c, d)
        cash += val * (1 - FEE)
        ret = None
        p = px(adj.get(c), d)
        if p and holds[c]["entry_px"]:
            ret = round((p / holds[c]["entry_px"] - 1) * 100, 1)
        trades.append({"date": d, "action": "SELL", "code": c, "name": holds[c]["name"],
                       "amount": round(val), "return_pct": ret, "reason": reason})
        del holds[c]

    def buy(c, d, amount):
        nonlocal cash
        p = px(adj.get(c), d)
        if p is None or amount <= 0 or cash < amount:
            return
        cash -= amount
        cash -= amount * FEE
        holds[c] = {"name": name_of.get(c), "entry_date": d, "entry_px": p, "invested": amount}
        trades.append({"date": d, "action": "BUY", "code": c, "name": name_of.get(c), "amount": round(amount)})

    for d in cal:
        # 日次: 暴落ストップ & 最長保有
        for c in list(holds):
            p = px(adj.get(c), d)
            ep = holds[c]["entry_px"]
            if p and ep:
                if p / ep - 1 <= DISASTER:
                    sell(c, d, "暴落ストップ-25%")
                    continue
            held_days = (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(holds[c]["entry_date"])).days
            if held_days > MAX_HOLD_DAYS:
                sell(c, d, "最長保有12ヶ月")

        # リバランス日: 入替
        if d in rebal_dates:
            targets = rebal_by_date[d]
            tset = set(targets[:N_HOLD])
            for c in list(holds):
                if c not in tset:
                    sell(c, d, "ランク外")
            per = total_equity(d) / N_HOLD
            for c in targets:
                if len(holds) >= N_HOLD:
                    break
                if c in holds:
                    continue
                amt = min(per, cash / (1 + FEE))
                if amt >= per * 0.5 and px(adj.get(c), d) is not None:
                    buy(c, d, amt)

        eq = round(total_equity(d))
        nkp = px(nk, d)
        bench = round(START_CAPITAL * (nkp / nk_start)) if (nkp and nk_start) else None
        equity_curve.append({"date": d, "value": eq, "bench": bench})

    latest = cal[-1]
    cur_holdings = []
    for c, h in holds.items():
        p = px(adj.get(c), latest)
        val = hold_value(c, latest)
        ret = round((p / h["entry_px"] - 1) * 100, 1) if (p and h["entry_px"]) else None
        cur_holdings.append({"code": c, "name": h["name"], "entry_date": h["entry_date"],
                             "invested": round(h["invested"]), "value": round(val), "return_pct": ret})
    cur_holdings.sort(key=lambda x: -(x["return_pct"] or -999))

    final = equity_curve[-1]["value"] if equity_curve else START_CAPITAL
    bench_final = equity_curve[-1]["bench"] if equity_curve else START_CAPITAL
    total_ret = round((final / START_CAPITAL - 1) * 100, 2)
    bench_ret = round((bench_final / START_CAPITAL - 1) * 100, 2) if bench_final else None
    peak = 0
    mdd = 0
    for e in equity_curve:
        peak = max(peak, e["value"])
        if peak:
            mdd = min(mdd, e["value"] / peak - 1)

    out = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "rule": "割安×質 v1 / 10銘柄均等 / 月次リバランス / -25%ストップ・最長12ヶ月 / 往復0.2%",
        "start_date": cal[0], "as_of": latest, "start_capital": START_CAPITAL,
        "final_value": final, "total_return_pct": total_ret,
        "bench_final": bench_final, "bench_return_pct": bench_ret,
        "excess_pct": round(total_ret - bench_ret, 2) if bench_ret is not None else None,
        "max_drawdown_pct": round(mdd * 100, 1),
        "cash": round(cash), "n_holdings": len(holds),
        "rebalance_dates": list(rebal_by_date),
        "holdings": cur_holdings,
        "equity_curve": equity_curve,
        "trades": trades[-60:],
        "n_trades": len(trades),
    }
    (ROOT / "data" / "portfolio.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"portfolio.json 生成: ¥{START_CAPITAL:,}→¥{final:,}（{total_ret:+.2f}%） "
          f"日経{bench_ret:+.2f}% 超過{out['excess_pct']}% / 最大DD{out['max_drawdown_pct']}% "
          f"/ 現在{len(holds)}銘柄・現金¥{round(cash):,} / 取引{len(trades)}回")


if __name__ == "__main__":
    main()
