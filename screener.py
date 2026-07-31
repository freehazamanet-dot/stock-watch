#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
東証プライム全銘柄 割安スクリーナー
- JPX公式の上場銘柄一覧(data_j.xls)からプライム市場の全銘柄を取得
- Yahoo Finance(yfinance)で指標を取得し、割安スコア(0-100)を算出
- バリュートラップ（割安に見えて危険）の警告フラグも付与
- 結果: data/latest.json / data/latest.csv / history/YYYY-MM-DD.json

使い方:
  ./venv/bin/python screener.py                # 全銘柄
  ./venv/bin/python screener.py --limit 30     # テスト用に30銘柄
  ./venv/bin/python screener.py --workers 6    # 同時取得数の調整

※本ツールは公開データに基づく機械的なスクリーニングであり、投資助言ではありません。
"""
import argparse
import csv
import datetime
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HIST = ROOT / "history"
LOGS = ROOT / "logs"
for d in (DATA, HIST, LOGS):
    d.mkdir(exist_ok=True)

JPX_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
UNDERVALUED_MIN = 60   # このスコア以上を「割安」と判定
STRICT_MIN = 70        # 厳選リスト（フラグ無し）の下限


# ---------------------------------------------------------------- universe
def load_universe(refresh_days: int = 7):
    """JPX公式の上場銘柄一覧からプライム市場銘柄を取得（週1回更新）"""
    import pandas as pd

    xls = DATA / "data_j.xls"
    need = (not xls.exists()) or (time.time() - xls.stat().st_mtime > refresh_days * 86400)
    if need:
        print("JPX銘柄一覧をダウンロード中...")
        req = urllib.request.Request(JPX_URL, headers={"User-Agent": "Mozilla/5.0"})
        xls.write_bytes(urllib.request.urlopen(req, timeout=120).read())

    df = pd.read_excel(xls)
    df = df[df["市場・商品区分"].astype(str).str.startswith("プライム")]
    uni = []
    for _, r in df.iterrows():
        code = str(r["コード"]).strip()
        uni.append({
            "code": code,
            "name": str(r["銘柄名"]).strip(),
            "sector": str(r.get("33業種区分", "")).strip(),
        })
    # 控えとして保存
    with open(DATA / "universe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "sector"])
        w.writeheader()
        w.writerows(uni)
    print(f"プライム市場: {len(uni)}銘柄")
    return uni


# ---------------------------------------------------------------- fetch
def fetch_one(code: str):
    """yfinanceで1銘柄の指標を取得（429等はバックオフ付きリトライ）"""
    import yfinance as yf

    for attempt in range(3):
        try:
            info = yf.Ticker(f"{code}.T").info
            if info and (info.get("currentPrice") or info.get("previousClose") or info.get("regularMarketPrice")):
                return info
            return None
        except Exception as e:
            msg = str(e)
            wait = 2.0 * (attempt + 1)
            if "429" in msg or "Too Many" in msg or "Rate" in msg:
                wait += 8.0
            time.sleep(wait)
    return None


def fnum(v):
    """有効な数値だけ通す"""
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


def extract(code, name, sector, info):
    """info辞書から指標を正規化して取り出す"""
    price = fnum(info.get("currentPrice")) or fnum(info.get("regularMarketPrice")) or fnum(info.get("previousClose"))
    per = fnum(info.get("trailingPE"))
    pbr = fnum(info.get("priceToBook"))
    eps = fnum(info.get("trailingEps"))

    # 配当利回り: 年間配当額/株価 から自前計算（yfinanceの単位ゆれ対策）
    div_rate = fnum(info.get("trailingAnnualDividendRate")) or fnum(info.get("dividendRate"))
    if div_rate and price:
        dy = div_rate / price * 100.0
    else:
        dy = fnum(info.get("dividendYield"))
        if dy is not None and dy <= 0.25:   # 比率(0.034)表記なら%へ
            dy *= 100.0
    if dy is not None and (dy < 0 or dy > 25):
        dy = None

    roe = fnum(info.get("returnOnEquity"))
    if roe is not None and abs(roe) <= 1.5:  # 比率表記なら%へ
        roe *= 100.0
    if roe is not None and abs(roe) > 200:
        roe = None

    d2e = fnum(info.get("debtToEquity"))     # %表記（50 = 50%）
    mcap = fnum(info.get("marketCap"))
    hi = fnum(info.get("fiftyTwoWeekHigh"))
    lo = fnum(info.get("fiftyTwoWeekLow"))
    pos52 = None
    if price and hi and lo and hi > lo:
        pos52 = max(0.0, min(1.0, (price - lo) / (hi - lo)))

    revg = fnum(info.get("revenueGrowth"))
    if revg is not None and abs(revg) <= 1.5:
        revg *= 100.0
    earng = fnum(info.get("earningsGrowth"))
    if earng is not None and abs(earng) <= 5:
        earng *= 100.0

    return {
        "code": code, "name": name, "sector": sector,
        "price": price, "per": per, "pbr": pbr, "eps": eps,
        "yield": dy, "roe": roe, "d2e": d2e, "mcap": mcap,
        "hi52": hi, "lo52": lo, "pos52": pos52,
        "revg": revg, "earng": earng,
    }


# ---------------------------------------------------------------- scoring
def score_one(r):
    """割安スコア(0-100)と内訳、警告フラグを算出"""
    parts = {}

    pbr = r["pbr"]
    if pbr is not None and pbr > 0:
        parts["PBR"] = 25 if pbr < 0.6 else 20 if pbr < 0.8 else 15 if pbr < 1.0 else 8 if pbr < 1.3 else 0
    else:
        parts["PBR"] = 0

    per = r["per"]
    if per is not None and per > 0:
        parts["PER"] = 25 if per < 8 else 20 if per < 10 else 15 if per < 12 else 8 if per < 15 else 0
    else:
        parts["PER"] = 0

    dy = r["yield"]
    parts["配当"] = 0 if dy is None else (20 if dy >= 4 else 15 if dy >= 3 else 8 if dy >= 2 else 0)

    roe = r["roe"]
    parts["ROE"] = 0 if roe is None else (15 if roe >= 10 else 12 if roe >= 8 else 6 if roe >= 5 else 0)

    pos = r["pos52"]
    parts["下値圏"] = 0 if pos is None else (10 if pos <= 0.30 else 6 if pos <= 0.45 else 0)

    d2e = r["d2e"]
    parts["財務"] = 0 if d2e is None else (5 if d2e < 50 else 3 if d2e < 100 else 0)

    score = sum(parts.values())

    flags = []
    if r["eps"] is not None and r["eps"] <= 0:
        flags.append("赤字(EPS≤0)")
    if r["per"] is None or (r["per"] is not None and r["per"] <= 0):
        if "赤字(EPS≤0)" not in flags and r["pbr"] is not None:
            flags.append("PER算出不可")
    if r["earng"] is not None and r["earng"] < -30:
        flags.append(f"利益急減({r['earng']:.0f}%)")
    if r["revg"] is not None and r["revg"] < -15:
        flags.append(f"売上減({r['revg']:.0f}%)")
    if dy is not None and dy > 6.5:
        flags.append("配当過大(減配リスク)")
    if pbr is not None and pbr < 0.4 and (roe is not None and roe < 3):
        flags.append("低PBR×低ROE(万年割安)")

    r["score"] = score
    r["parts"] = parts
    r["flags"] = flags
    return r


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭N銘柄のみ（テスト用）")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    t0 = time.time()
    uni = load_universe()
    if args.limit:
        uni = uni[: args.limit]

    # 前回の割安リスト（差分検出用）
    prev_codes = set()
    prev_file = DATA / "latest.json"
    if prev_file.exists():
        try:
            prev = json.loads(prev_file.read_text(encoding="utf-8"))
            prev_codes = {x["code"] for x in prev.get("results", []) if x.get("score", 0) >= UNDERVALUED_MIN}
        except Exception:
            pass

    results, failed = [], []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_one, u["code"]): u for u in uni}
        for fut in as_completed(futs):
            u = futs[fut]
            done += 1
            info = fut.result()
            if info:
                results.append(score_one(extract(u["code"], u["name"], u["sector"], info)))
            else:
                failed.append(u["code"])
            if done % 100 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(uni)} 取得済 ({el:.0f}s, 失敗{len(failed)})", flush=True)

    results.sort(key=lambda x: (-x["score"], x["pbr"] if x["pbr"] is not None else 99))

    under = [r for r in results if r["score"] >= UNDERVALUED_MIN]
    cur_codes = {r["code"] for r in under}
    new_in = sorted(cur_codes - prev_codes)
    out = sorted(prev_codes - cur_codes)

    today = datetime.date.today().isoformat()
    payload = {
        "date": today,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "universe": len(uni),
        "fetched": len(results),
        "failed": len(failed),
        "undervalued_min": UNDERVALUED_MIN,
        "strict_min": STRICT_MIN,
        "new_in": new_in,
        "dropped": out,
        "results": results,
    }
    (DATA / "latest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (HIST / f"{today}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    cols = ["code", "name", "sector", "price", "per", "pbr", "yield", "roe", "d2e",
            "pos52", "mcap", "score", "flags"]
    with open(DATA / "latest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in results:
            w.writerow([r["code"], r["name"], r["sector"], r["price"], r["per"], r["pbr"],
                        r["yield"], r["roe"], r["d2e"], r["pos52"], r["mcap"], r["score"],
                        " / ".join(r["flags"])])

    el = time.time() - t0
    print(f"\n完了: {len(results)}/{len(uni)}銘柄 取得（{el/60:.1f}分, 失敗{len(failed)}）")
    print(f"割安(スコア{UNDERVALUED_MIN}+): {len(under)}銘柄 ／ 新規イン {len(new_in)} ／ アウト {len(out)}")
    strict = [r for r in under if r["score"] >= STRICT_MIN and not r["flags"]]
    print(f"厳選(スコア{STRICT_MIN}+・警告なし): {len(strict)}銘柄")
    for r in strict[:15]:
        print(f"  {r['code']} {r['name']}  score={r['score']} PBR={r['pbr']:.2f} "
              f"PER={r['per']:.1f} 配当={r['yield'] or 0:.1f}% ROE={r['roe'] or 0:.1f}%")


if __name__ == "__main__":
    main()
