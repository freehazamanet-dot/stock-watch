#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""¥100万 ルール運用シミュレーション（ペーパーポートフォリオ）

ルール v2（割安×質・100株単位で実際に買える版）:
- 買い候補: 厳選(score>=strict_min & 警告フラグなし) かつ ROE>=8% を ROE降順（=質のティルト）
- 売買単位: 日本株の原則である単元株=100株の整数倍でのみ購入（実際に発注できる形）
- 保有: 上位から、1銘柄の目安予算(=総資産/10≒10万円)に収まる単元数を購入。最大10銘柄。
  100株で予算を超える高株価銘柄はスキップして次の候補へ。余った現金は上位保有へ単元追加、
  それでも余れば現金で保有（=無理に10万円ちょうどにしない、実運用どおり）。
- リバランス: 28日以上あけて（≒月次）、上位10から外れた保有を売り、上位で埋め直す
- 売り: -25%で暴落ストップ（日次）、最長365日で入替、それ以外はリバランス主導
- コスト: 売買ごとに0.1%（往復0.2%）控除
- 起点: 最古スナップショット(6/13→最初の営業日6/15)から遡及シミュ＋以降フォワード。日次評価
- 価格: 購入金額=実株価(Close)、評価=分割配当調整済(Adj Close)のトータルリターン基準
- ベンチマーク: 同額を日経平均(^N225)に投じて日次評価

注意: S+/S深掘りランクは過去分が保存されていないため、履歴から再現可能な近似(厳選×ROE)を使用。
※単元未満株(S株等)を使えば10万円ちょうどの均等も可能だが、ここは標準の100株単位で検証。
出力: data/portfolio.json
"""
import json
import glob
import os
import time
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

# ルールのバージョン（前向き適用の原則）:
# 改良するときは、この上のパラメータ（過去に適用済みの値）を書き換えず、
# 新ルールは「今日以降」だけに適用する。過去の成績を後から良く見せる=過剰最適化を
# 構造的に防ぐため。変更は月次レビューが根拠付きで提案し、承認を得てから反映する。
# 詳細な改良ポリシーは RULES.md を参照。
RULE_VERSION = "v2 (2026-06-13〜 / 100株単位・割安×質)"

# クラウド(GitHub Actions)ではscreener.pyの後にYahooがレート制限をかけるため、
# 取得成功時に価格をキャッシュへ保存し、失敗時はキャッシュから復元してシミュを継続する。
CACHE_PX = ROOT / "data" / "px_cache.json"     # {code: {date: adj_close}}
CACHE_NK = ROOT / "data" / "nikkei.json"        # {date: adj_close}


def _load_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _retry(fn, tries=3, base=8):
    """空でない結果が返るまでリトライ（レート制限のバックオフ）。全滅なら None。"""
    for i in range(tries):
        try:
            r = fn()
            if r:
                return r
        except Exception as e:
            print(f"  [warn] 取得試行{i+1}失敗: {e}")
        if i < tries - 1:
            time.sleep(base * (i + 1))
    return None


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


def _dl_prices(codes, start_date):
    """各銘柄の [実株価Close, 調整株価AdjClose] を取得。
    実株価=単元(100株)の購入金額計算用、調整株価=分割/配当込みの評価用。"""
    import yfinance as yf
    start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    df = yf.download([f"{c}.T" for c in codes], start=start, end=end, auto_adjust=False,
                     progress=False, threads=True, group_by="ticker")
    out = {}
    for c in codes:
        t = f"{c}.T"
        try:
            if t not in df.columns.get_level_values(0):
                continue
            s = {}
            for ts, row in df[t].iterrows():
                raw = row.get("Close")
                adj = row.get("Adj Close")
                if raw == raw and adj == adj:   # NaN 除外
                    s[ts.date().isoformat()] = [float(raw), float(adj)]
            if s:
                out[c] = s
        except Exception:
            continue
    return out


def fetch_prices(codes, start_date):
    """取得成功→キャッシュ更新して返す。失敗→キャッシュから復元（レート制限耐性）。"""
    cache = _load_json(CACHE_PX)
    live = _retry(lambda: _dl_prices(codes, start_date))
    if live:
        cache.update(live)   # 銘柄単位でフルシリーズを差し替え
        CACHE_PX.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    else:
        print(f"  [warn] 価格はライブ取得できずキャッシュ({len(cache)}銘柄)を使用")
    return {c: cache[c] for c in codes if c in cache}


def _dl_nikkei(start_date):
    import yfinance as yf
    start = (datetime.date.fromisoformat(start_date) - datetime.timedelta(days=7)).isoformat()
    end = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    h = yf.Ticker("^N225").history(start=start, end=end, auto_adjust=True)
    return {ts.date().isoformat(): float(r["Close"]) for ts, r in h.iterrows()}


def nikkei_series(start_date):
    live = _retry(lambda: _dl_nikkei(start_date))
    if live:
        CACHE_NK.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")
        return live
    cache = _load_json(CACHE_NK)
    if cache:
        print(f"  [warn] 日経はライブ取得できずキャッシュ({len(cache)}日)を使用")
    return cache


def px(series, date_str):
    """その日以前で最も近い値（キャリーフォワード）。日経など単一値シリーズ用。"""
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


def _pair(series, date_str):
    """[実株価, 調整株価] をキャリーフォワードで返す。旧形式(単一float)にも耐性。"""
    v = px(series, date_str)
    if v is None:
        return None, None
    if isinstance(v, (list, tuple)):
        return v[0], v[1]
    return None, v   # 旧キャッシュ（調整のみ）: 実株価不明→単元購入不可、評価は可能


def raw_px(series, date_str):
    return _pair(series, date_str)[0]


def adj_px(series, date_str):
    return _pair(series, date_str)[1]


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
    rebal_targets = [(s["date"], target_list(s)) for s in rebal]
    name_of = {}
    for s in snaps:
        for c, r in s["bycode"].items():
            name_of[c] = r.get("name")

    # 保有しうる全銘柄の価格を取得（高株価はスキップされるので候補は広めに）
    universe = sorted({c for _, tl in rebal_targets for c in tl[:N_HOLD * 6]})
    print(f"  価格取得: {len(universe)}銘柄...")
    px_data = fetch_prices(universe, start_date)
    nk = nikkei_series(start_date)

    # 取引カレンダー = 日経の営業日（起点以降）
    cal = sorted(d for d in nk if d >= start_date)
    if not cal:
        print("カレンダー取得失敗")
        return
    nk_start = px(nk, cal[0])

    # スナップショット日を「その日以降で最初の営業日」に対応づけてリバランスを発火。
    # （例: 6/13は土曜=非営業日→6/15で発火。これがないと初回買付が丸ごと欠落し、
    #   最初のリバランスまで全額現金のままになってしまう）
    rebal_by_date = {}
    for snap_date, targets in rebal_targets:
        tday = next((d for d in cal if d >= snap_date), None)
        if tday:
            rebal_by_date[tday] = targets

    cash = START_CAPITAL
    holds = {}   # code -> {name, first_date, lots:[{shares, entry_raw, entry_adj, invested, date}]}
    trades = []
    equity_curve = []
    rebal_dates = set(rebal_by_date)
    UNIT = 100    # 単元株数（日本株は原則100株単位）

    def pos_shares(c):
        return sum(l["shares"] for l in holds[c]["lots"])

    def pos_invested(c):
        return sum(l["invested"] for l in holds[c]["lots"])

    def pos_value(c, d):
        pa = adj_px(px_data.get(c), d)
        if pa is None:
            return pos_invested(c)
        return sum(l["invested"] * (pa / l["entry_adj"]) for l in holds[c]["lots"] if l["entry_adj"])

    def total_equity(d):
        return cash + sum(pos_value(c, d) for c in holds)

    def sell(c, d, reason):
        nonlocal cash
        val = pos_value(c, d)
        inv = pos_invested(c)
        cash += val * (1 - FEE)
        ret = round((val / inv - 1) * 100, 1) if inv else None
        trades.append({"date": d, "action": "SELL", "code": c, "name": holds[c]["name"],
                       "shares": pos_shares(c), "amount": round(val), "cost": round(inv),
                       "pl": round(val - inv), "return_pct": ret, "reason": reason})
        del holds[c]

    def buy(c, d, lots):
        """100株×lots単元を購入。実株価で約定・評価は調整株価で追跡。成功でTrue。"""
        nonlocal cash
        pr = raw_px(px_data.get(c), d)
        pa = adj_px(px_data.get(c), d)
        if pr is None or pa is None or lots < 1:
            return False
        shares = lots * UNIT
        cost = shares * pr
        if cost * (1 + FEE) > cash + 1e-6:
            return False
        cash -= cost * (1 + FEE)
        h = holds.get(c)
        if h is None:
            h = holds[c] = {"name": name_of.get(c), "first_date": d, "lots": []}
        h["lots"].append({"shares": shares, "entry_raw": pr, "entry_adj": pa,
                          "invested": cost, "date": d})
        trades.append({"date": d, "action": "BUY", "code": c, "name": name_of.get(c),
                       "shares": shares, "price": round(pr, 1), "amount": round(cost)})
        return True

    def do_rebalance(d, targets):
        # ランク外（上位N_HOLDから外れた保有）を売却
        tset = set(targets[:N_HOLD])
        for c in list(holds):
            if c not in tset:
                sell(c, d, "ランク外")
        equity = total_equity(d)
        slot = equity / N_HOLD          # 1銘柄の目安予算（≒10万円）
        cap = slot * 1.5                # 1銘柄の上限（100株の端数で少し超えるのは許容）
        # パス1: 目標銘柄を上位から、目安予算に収まる単元数だけ新規購入
        for c in targets:
            if len(holds) >= N_HOLD:
                break
            if c in holds:
                continue
            pr = raw_px(px_data.get(c), d)
            if pr is None:
                continue
            lotcost = pr * UNIT
            if lotcost > cap:
                continue               # 100株で予算超過（株価が高い）→スキップして次の候補へ
            want = max(1, int(slot // lotcost))
            while want >= 1 and want * lotcost * (1 + FEE) > cash:
                want -= 1
            if want >= 1:
                buy(c, d, want)
        # パス2: 余った現金を上位保有へ単元追加（上限capまで）し均等に近づける
        progress = True
        while progress:
            progress = False
            for c in targets:
                if c not in holds:
                    continue
                pr = raw_px(px_data.get(c), d)
                if pr is None:
                    continue
                lotcost = pr * UNIT
                if pos_value(c, d) + lotcost <= cap and lotcost * (1 + FEE) <= cash:
                    if buy(c, d, 1):
                        progress = True

    for d in cal:
        # 日次: 暴落ストップ & 最長保有
        for c in list(holds):
            inv = pos_invested(c)
            val = pos_value(c, d)
            if inv and val / inv - 1 <= DISASTER:
                sell(c, d, "暴落ストップ-25%")
                continue
            held_days = (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(holds[c]["first_date"])).days
            if held_days > MAX_HOLD_DAYS:
                sell(c, d, "最長保有12ヶ月")

        # リバランス日: 入替
        if d in rebal_dates:
            do_rebalance(d, rebal_by_date[d])

        eq = round(total_equity(d))
        nkp = px(nk, d)
        bench = round(START_CAPITAL * (nkp / nk_start)) if (nkp and nk_start) else None
        equity_curve.append({"date": d, "value": eq, "bench": bench})

    latest = cal[-1]
    cur_holdings = []
    for c, h in holds.items():
        inv = pos_invested(c)
        val = pos_value(c, latest)
        ret = round((val / inv - 1) * 100, 1) if inv else None
        cur_holdings.append({"code": c, "name": h["name"], "entry_date": h["first_date"],
                             "shares": pos_shares(c), "invested": round(inv),
                             "value": round(val), "return_pct": ret})
    cur_holdings.sort(key=lambda x: -(x["return_pct"] or -999))

    # 本日のアクション（bot出力）＝最終営業日に発生した売買。通常は空＝保有継続。
    today_actions = [t for t in trades if t["date"] == latest]
    # 次回リバランスの目安（最後のリバランス営業日 + 最短間隔）
    last_rebal = max(rebal_by_date) if rebal_by_date else start_date
    next_rebal_est = (datetime.date.fromisoformat(last_rebal) + datetime.timedelta(days=REBAL_MIN_DAYS)).isoformat()

    # 損益の内訳（分かりやすい説明用）
    sold = [t for t in trades if t["action"] == "SELL"]
    realized_pl = sum(t.get("pl", 0) for t in sold)                      # 入替で確定した損益
    unrealized_pl = sum(h["value"] - h["invested"] for h in cur_holdings)  # 保有中の含み損益

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
        "rule": "割安×質 v1 / 100株単位で最大10銘柄・約10万円ずつ / 月次リバランス / -25%ストップ・最長12ヶ月 / 往復0.2%",
        "unit": UNIT,
        "rule_version": RULE_VERSION,
        "today_actions": today_actions, "next_rebalance_est": next_rebal_est,
        "start_date": cal[0], "as_of": latest, "start_capital": START_CAPITAL,
        "final_value": final, "total_return_pct": total_ret,
        "bench_final": bench_final, "bench_return_pct": bench_ret,
        "excess_pct": round(total_ret - bench_ret, 2) if bench_ret is not None else None,
        "max_drawdown_pct": round(mdd * 100, 1),
        "cash": round(cash), "n_holdings": len(holds),
        "total_pl": round(final - START_CAPITAL),
        "realized_pl": round(realized_pl), "unrealized_pl": round(unrealized_pl),
        "rebalance_dates": sorted(rebal_by_date),
        "holdings": cur_holdings,
        "sold": [{"code": t["code"], "name": t["name"], "date": t["date"], "shares": t.get("shares"),
                  "pl": t.get("pl"), "return_pct": t.get("return_pct"), "reason": t.get("reason")}
                 for t in sold],
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
