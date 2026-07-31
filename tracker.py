#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""割安判定の追跡（シグナル成績）

history/*.json の各スナップショットから「初めて割安(スコア>=undervalued_min)と
判定された日」を各銘柄のエントリーとし、その後の成績を集計して data/tracking.json に出力。

- エントリー/現在の表示株価: スナップショットの実株価（判定時・最新）
- リターン: yfinance の分割・配当調整済み終値で計算（= トータルリターン、分割ズレを排除）
  取得に失敗した銘柄のみ、実株価ベースにフォールバック（split_suspectで異常値を除外）
- ベンチマーク: 日経平均(^N225) 同期間騰落率との差（超過リターン=α）
"""
import json
import glob
import os
import datetime
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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
        snaps.append({
            "date": date,
            "under_min": d.get("undervalued_min", 60),
            "strict_min": d.get("strict_min", 70),
            "bycode": bycode,
        })
    snaps.sort(key=lambda s: s["date"])
    return snaps


def _series_from_hist(hist, colname="Close"):
    out = {}
    for ts, row in hist.iterrows():
        try:
            out[ts.date().isoformat()] = float(row[colname])
        except Exception:
            pass
    return out


def close_on_or_before(series, date_str, span=10):
    if not series:
        return None
    d = date_str
    for _ in range(span):
        if d in series:
            return series[d]
        d = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat()
    return None


def latest_close(series):
    return series[max(series)] if series else None


def fetch_adjusted(codes, start_date):
    """コード -> {日付: 調整後終値} を一括取得（分割・配当調整済み）。失敗時は空。"""
    try:
        import yfinance as yf
        start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
        end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        tickers = [f"{c}.T" for c in codes]
        df = yf.download(tickers, start=start, end=end, auto_adjust=True,
                         progress=False, threads=True, group_by="ticker")
        out = {}
        for c in codes:
            t = f"{c}.T"
            try:
                sub = df[t] if t in df.columns.get_level_values(0) else None
                if sub is None:
                    continue
                s = {}
                for ts, row in sub.iterrows():
                    v = row.get("Close")
                    if v == v:  # not NaN
                        s[ts.date().isoformat()] = float(v)
                if s:
                    out[c] = s
            except Exception:
                continue
        return out
    except Exception as e:
        print(f"  [warn] 調整後株価の一括取得に失敗、実株価にフォールバック: {e}")
        return {}


def nikkei_series(start_date):
    try:
        import yfinance as yf
        start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
        end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        return _series_from_hist(yf.Ticker("^N225").history(start=start, end=end, auto_adjust=True))
    except Exception as e:
        print(f"  [warn] 日経平均の取得に失敗: {e}")
        return {}


SPLIT_RATIOS = [2, 3, 4, 5, 10, 1.5, 0.5, 1 / 3, 0.25, 0.2, 0.1]


def split_suspect(entry, current):
    if not entry or not current:
        return False
    ratio = current / entry
    return any(abs(ratio - r) / r < 0.06 for r in SPLIT_RATIOS)


def main():
    snaps = load_snapshots()
    if not snaps:
        print("スナップショットがありません")
        return
    latest = snaps[-1]
    latest_date = latest["date"]
    earliest_date = snaps[0]["date"]

    # 各銘柄の初回割安判定を特定
    signals = {}
    for s in snaps:
        um, sm = s["under_min"], s["strict_min"]
        for c, r in s["bycode"].items():
            sc = r.get("score")
            if sc is None or sc < um:
                continue
            if c not in signals:
                signals[c] = {
                    "code": c, "name": r.get("name"), "sector": r.get("sector"),
                    "entry_date": s["date"], "entry_price": r.get("price"),
                    "entry_score": sc,
                    "was_strict": bool(sc >= sm and not r.get("flags")),
                }

    print(f"  対象{len(signals)}銘柄の調整後株価を取得中...")
    adj = fetch_adjusted(list(signals.keys()), earliest_date)
    nk = nikkei_series(earliest_date)
    nk_latest = close_on_or_before(nk, latest_date)

    for c, sig in signals.items():
        cur = latest["bycode"].get(c)
        sig["current_price"] = cur.get("price") if cur else None
        sig["current_score"] = cur.get("score") if cur else None
        sig["still_uv"] = bool(cur and cur.get("score") is not None and cur.get("score") >= latest["under_min"])
        sig["in_universe"] = cur is not None
        ep, cp = sig["entry_price"], sig["current_price"]

        # リターン: まず調整後株価、無ければ実株価
        aser = adj.get(c)
        a_entry = close_on_or_before(aser, sig["entry_date"]) if aser else None
        a_cur = latest_close(aser) if aser else None
        sig["adjusted"] = bool(a_entry and a_cur)
        if a_entry and a_cur:
            sig["return_pct"] = round((a_cur / a_entry - 1) * 100, 2)
            sig["split_suspect"] = False  # 調整済みなので分割の心配なし
            # 実株価比と乖離が大きい＝期間中に分割/大幅配当あり
            if ep and cp:
                raw_r = (cp / ep - 1) * 100
                sig["split_note"] = abs(raw_r - sig["return_pct"]) > 8
            else:
                sig["split_note"] = False
        else:
            sig["return_pct"] = round((cp / ep - 1) * 100, 2) if (ep and cp) else None
            sig["split_suspect"] = split_suspect(ep, cp)
            sig["split_note"] = sig["split_suspect"]

        d0 = datetime.date.fromisoformat(sig["entry_date"])
        d1 = datetime.date.fromisoformat(latest_date)
        sig["days"] = (d1 - d0).days
        nk_entry = close_on_or_before(nk, sig["entry_date"])
        if nk_entry and nk_latest:
            sig["bench_return_pct"] = round((nk_latest / nk_entry - 1) * 100, 2)
            sig["excess_pct"] = round(sig["return_pct"] - sig["bench_return_pct"], 2) \
                if sig["return_pct"] is not None else None
        else:
            sig["bench_return_pct"] = None
            sig["excess_pct"] = None

    rows = sorted(signals.values(),
                  key=lambda x: (x["return_pct"] is None, -(x["return_pct"] or 0)))
    # 集計は「保有日数>0 かつ 分割疑い(未調整)でない かつ リターン算出可」
    scored = [r for r in rows if r["return_pct"] is not None and r["days"] > 0 and not r["split_suspect"]]

    def agg(vals):
        return {
            "n": len(vals),
            "win_rate": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1) if vals else None,
            "avg": round(statistics.mean(vals), 2) if vals else None,
            "median": round(statistics.median(vals), 2) if vals else None,
            "best": round(max(vals), 2) if vals else None,
            "worst": round(min(vals), 2) if vals else None,
        }

    rets = [r["return_pct"] for r in scored]
    excs = [r["excess_pct"] for r in scored if r["excess_pct"] is not None]
    strict_rets = [r["return_pct"] for r in scored if r["was_strict"]]

    summary = {
        "as_of": latest_date,
        "snapshot_dates": [s["date"] for s in snaps],
        "n_snapshots": len(snaps),
        "n_signals_total": len(rows),
        "n_scored": len(scored),
        "n_adjusted": sum(1 for r in rows if r.get("adjusted")),
        "all": agg(rets),
        "strict": agg(strict_rets),
        "avg_excess_vs_nikkei": round(statistics.mean(excs), 2) if excs else None,
        "median_excess_vs_nikkei": round(statistics.median(excs), 2) if excs else None,
        "bench_return_pct": scored[0]["bench_return_pct"] if scored else None,
        "still_uv_count": sum(1 for r in rows if r["still_uv"]),
        "split_suspect_count": sum(1 for r in rows if r["split_suspect"]),
    }

    out = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
           "summary": summary, "signals": rows}
    (ROOT / "data" / "tracking.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")

    a = summary["all"]
    print(f"tracking.json 生成: 追跡{summary['n_signals_total']} / 集計{a['n']}"
          f"（調整済{summary['n_adjusted']}） / 勝率{a['win_rate']}% / 平均{a['avg']}%"
          f" / 中央値{a['median']}% / 日経{summary['bench_return_pct']}% / 超過{summary['avg_excess_vs_nikkei']}%"
          f" / best{a['best']}% worst{a['worst']}%")


if __name__ == "__main__":
    main()
