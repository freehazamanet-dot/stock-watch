#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data/latest.json から監視ダッシュボード index.html を生成"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))

UNDER = data["undervalued_min"]
STRICT = data["strict_min"]
results = data["results"]
under = [r for r in results if r["score"] >= UNDER]
strict = [r for r in under if r["score"] >= STRICT and not r["flags"]]
caution = [r for r in under if r["flags"]]
name_of = {r["code"]: r["name"] for r in results}

payload_js = json.dumps(
    [{k: r.get(k) for k in ("code", "name", "sector", "price", "per", "pbr", "yield",
                            "roe", "d2e", "pos52", "mcap", "score", "flags")} for r in results],
    ensure_ascii=False)

new_in = [(c, name_of.get(c, "")) for c in data.get("new_in", [])]
dropped = [(c, name_of.get(c, "")) for c in data.get("dropped", [])]

# 決算深掘り結果（あれば）
deep_path = ROOT / "data" / "deep.json"
deep = json.loads(deep_path.read_text(encoding="utf-8")) if deep_path.exists() else {"results": []}
deep_results = deep.get("results", [])
DEEP_FIELDS = ("code", "name", "sector", "price", "per", "pbr", "yield", "roe",
               "base", "quality", "total", "tier", "consec_profit", "consec_growth",
               "op_margin", "net_margin", "roa", "payout", "net_cash_ratio",
               "ocf_margin", "gates")
deep_js = json.dumps([{k: r.get(k) for k in DEEP_FIELDS} for r in deep_results], ensure_ascii=False)
sp_count = len([r for r in deep_results if r["tier"] == "S+"])
s_count = len([r for r in deep_results if r["tier"] == "S"])
has_deep = bool(deep_results)

# ニュース検証メモ（あれば）
notes_path = ROOT / "data" / "notes.json"
notes_obj = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
notes = notes_obj.get("notes", {})
notes_date = notes_obj.get("date", "")
notes_js = json.dumps(notes, ensure_ascii=False)

# 割安判定の追跡（シグナル成績）
track_path = ROOT / "data" / "tracking.json"
track_obj = json.loads(track_path.read_text(encoding="utf-8")) if track_path.exists() else None
has_track = bool(track_obj and track_obj.get("signals"))
track_js = json.dumps(track_obj, ensure_ascii=False) if track_obj else "null"
if has_track:
    tsum = track_obj["summary"]
    a = tsum["all"]
    st = tsum["strict"]
    bench = tsum.get("bench_return_pct") or 0
    exc = tsum.get("avg_excess_vs_nikkei") or 0
    dates = "・".join(tsum.get("snapshot_dates", []))
    track_section_html = f"""
  <h2>🎯 割安判定の追跡（成績）</h2>
  <p class="disclaimer">各銘柄が<b>初めて割安判定（スコア{UNDER}+）された日の株価</b>を起点に、その後の値動きを<b>分割・配当調整済み</b>で追跡。日経平均（同期間）との差＝超過リターン(α)。母集団はスナップショット {dates}（{tsum['n_snapshots']}点）。<b>単一期間・小標本の参考値</b>で、将来を保証しません。日次実行を続けるほど厚くなります。</p>
  <div class="kpis">
    <div class="kpi"><div class="n">{a['n']}</div><div class="l">集計銘柄</div></div>
    <div class="kpi"><div class="n {'green' if (a['win_rate'] or 0) >= 50 else ''}">{a['win_rate']}%</div><div class="l">勝率（プラス比率）</div></div>
    <div class="kpi"><div class="n {'green' if (a['avg'] or 0) > 0 else ''}">{a['avg']:+.1f}%</div><div class="l">平均リターン</div></div>
    <div class="kpi"><div class="n">{a['median']:+.1f}%</div><div class="l">中央値</div></div>
    <div class="kpi"><div class="n" style="color:var(--red)">{bench:+.1f}%</div><div class="l">日経平均（同期間）</div></div>
    <div class="kpi"><div class="n gold">{exc:+.1f}%</div><div class="l">超過リターン(α)</div></div>
  </div>
  <p class="muted" style="margin:-8px 0 8px;">厳選（{STRICT}+・警告なし）のみでも 勝率{st['win_rate']}% / 平均{st['avg']:+.1f}%（{st['n']}銘柄）。ベスト{a['best']:+.0f}% / ワースト{a['worst']:+.0f}%。まだ割安判定中: {tsum['still_uv_count']}銘柄。</p>
  <div class="controls">
    <input id="tq" placeholder="コード・社名で検索" style="flex:1;min-width:200px;">
    <select id="tsort">
      <option value="return_desc">リターン高い順</option>
      <option value="return_asc">リターン低い順</option>
      <option value="excess_desc">日経比 大きい順</option>
      <option value="entry_score_desc">判定スコア順</option>
    </select>
    <label class="muted" style="display:flex;align-items:center;gap:6px;"><input type="checkbox" id="tstrict" style="width:auto"> 厳選のみ</label>
  </div>
  <div class="card tablebox" style="max-height:520px;"><table id="t-track"></table></div>
"""
else:
    track_section_html = ""

# 要因分析（analysis.json）
analysis_path = ROOT / "data" / "analysis.json"
analysis = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else None
if analysis:
    conf = analysis.get("confidence", "")
    conf_label = {"medium": "中", "low": "低", "very_low": "非常に低い"}.get(conf, conf)
    ph = analysis.get("primary_horizon", {})
    hz_rows = ""
    for h in analysis.get("horizon_summary", []):
        if h.get("n"):
            hz_rows += (f'<tr><td>{h["horizon"]}</td><td class="num">{h["n"]}</td>'
                        f'<td class="num">{h.get("win_rate_vs_mkt")}%</td>'
                        f'<td class="num" style="color:{"var(--green)" if (h.get("avg_excess") or 0) > 0 else "var(--red)"};font-weight:700">{h.get("avg_excess"):+.2f}%</td>'
                        f'<td class="num muted">{(h.get("avg_return") or 0):+.2f}%</td></tr>')
        else:
            hz_rows += (f'<tr><td>{h["horizon"]}</td>'
                        f'<td class="num" colspan="4" style="text-align:center;color:var(--muted)">蓄積中（このホライズンに到達したシグナルがまだ無い）</td></tr>')
    ic_rows = ""
    for f in sorted(analysis.get("factor_ic", []), key=lambda x: -(x["signed_ic"] if x["signed_ic"] is not None else -9)):
        s = f.get("signed_ic")
        col = ("var(--muted)" if (not f.get("reliable") or s is None)
               else "var(--green)" if s > 0.05 else "var(--red)" if s < -0.05 else "var(--ink)")
        badge = "" if f.get("reliable") else ' <span class="muted" style="font-size:.7rem">(標本不足)</span>'
        ic_rows += (f'<tr><td>{f["label"]}{badge}</td>'
                    f'<td class="num" style="color:{col};font-weight:700">{("—" if s is None else f"{s:+.3f}")}</td>'
                    f'<td class="num muted">{f.get("n")}</td></tr>')
    findings_html = "".join(f"<li>{x}</li>" for x in analysis.get("findings", []))
    caveats_html = "".join(f"<li>{x}</li>" for x in analysis.get("caveats", []))
    analysis_section_html = f"""
  <h2>📈 成績の推移・要因分析（精度向上のエンジン）</h2>
  <p class="disclaimer">割安判定した銘柄が、判定日から <b>1ヶ月/3ヶ月/6ヶ月後</b> に日経平均をどれだけ上回ったか(超過リターン)を追跡。さらに<b>どの指標が実際にリターンを当てているか</b>を順位相関(signed IC=正で期待どおり効果あり)で計測。目標=<b>{analysis.get('target','')}</b>。主ホライズン=<b>{ph.get('label','')}</b>（標本n={ph.get('n')}・信頼度<b>{conf_label}</b>）。単一期間・小標本の暫定値で、蓄積で濃くなります。</p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">
    <div>
      <p class="muted" style="margin:0 0 4px;">ホライズン別 成績（対日経・超過）</p>
      <div class="card tablebox">
        <table><thead><tr><th>期間</th><th class="num">銘柄数</th><th class="num">対市場勝率</th><th class="num">平均超過</th><th class="num">平均リターン</th></tr></thead><tbody>{hz_rows}</tbody></table>
      </div>
    </div>
    <div>
      <p class="muted" style="margin:0 0 4px;">どの指標が効いているか（signed IC / 主ホライズン）</p>
      <div class="card tablebox">
        <table><thead><tr><th>指標</th><th class="num">効き(signed IC)</th><th class="num">n</th></tr></thead><tbody>{ic_rows}</tbody></table>
      </div>
    </div>
  </div>
  <div class="card" style="margin-top:12px;">
    <b style="color:var(--gold);font-size:.9rem;">改善候補（データドリブン・承認制）</b>
    <ul class="meth" style="margin:6px 0 0;padding-left:1.2em;">{findings_html}</ul>
    <b style="color:var(--muted);font-size:.8rem;display:block;margin-top:10px;">注意</b>
    <ul class="meth" style="margin:4px 0 0;padding-left:1.2em;font-size:.74rem;">{caveats_html}</ul>
  </div>
"""
else:
    analysis_section_html = ""

# ¥100万 ルール運用シミュレーション（portfolio.json）
pf_path = ROOT / "data" / "portfolio.json"
pf = json.loads(pf_path.read_text(encoding="utf-8")) if pf_path.exists() else None
if pf and pf.get("equity_curve"):
    ec = pf["equity_curve"]
    cap = pf.get("start_capital", 1_000_000)
    vals = [e["value"] for e in ec] + [e["bench"] for e in ec if e.get("bench")] + [cap]
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    W, H, pad = 760, 200, 10
    n = len(ec)

    def _pts(key):
        out = []
        for i, e in enumerate(ec):
            v = e.get(key)
            if v is None:
                continue
            x = pad + (i / max(n - 1, 1)) * (W - 2 * pad)
            y = pad + (1 - (v - lo) / rng) * (H - 2 * pad)
            out.append(f"{x:.1f},{y:.1f}")
        return " ".join(out)
    base_y = pad + (1 - (cap - lo) / rng) * (H - 2 * pad)
    svg = (f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" style="width:100%;height:200px;">'
           f'<line x1="{pad}" y1="{base_y:.1f}" x2="{W-pad}" y2="{base_y:.1f}" stroke="#3a4450" stroke-dasharray="4 4" stroke-width="1"/>'
           f'<polyline fill="none" stroke="#8b98a5" stroke-width="1.5" points="{_pts("bench")}"/>'
           f'<polyline fill="none" stroke="#3fb27f" stroke-width="2.5" points="{_pts("value")}"/>'
           f'</svg>')
    tr = pf.get("total_return_pct", 0)
    br = pf.get("bench_return_pct")
    exc = pf.get("excess_pct")
    hold_rows = ""
    for h in pf.get("holdings", []):
        r = h.get("return_pct")
        col = "var(--green)" if (r or 0) >= 0 else "var(--red)"
        sh = h.get("shares")
        sh_txt = f"{sh:,}株" if sh else "—"
        hold_rows += (f'<tr><td><a href="https://finance.yahoo.co.jp/quote/{h["code"]}.T" target="_blank">{h["code"]}</a></td>'
                      f'<td>{h.get("name","")}</td><td class="num">{sh_txt}</td><td class="num muted">{h.get("entry_date","")}</td>'
                      f'<td class="num">¥{h.get("invested",0):,}</td><td class="num">¥{h.get("value",0):,}</td>'
                      f'<td class="num" style="color:{col};font-weight:700">{("—" if r is None else f"{r:+.1f}%")}</td></tr>')
    # --- この結果になった理由（内訳） ---
    final_v = pf.get("final_value", cap)
    bench_v = pf.get("bench_final")
    total_pl = pf.get("total_pl", final_v - cap)
    realized = pf.get("realized_pl")
    unrealized = pf.get("unrealized_pl")
    cost_etc = (total_pl - (realized or 0) - (unrealized or 0)) if (realized is not None and unrealized is not None) else None
    rds = pf.get("rebalance_dates", [])
    buy_day = rds[0] if rds else pf.get("start_date", "")
    rebal_days = rds[1:] if len(rds) > 1 else []
    sold = pf.get("sold", [])

    def _yen(v):
        if v is None:
            return "—"
        c = "var(--green)" if v >= 0 else "var(--red)"
        return f'<span style="color:{c};font-weight:700">{v:+,}円</span>'

    sold_rows = ""
    for s in sold:
        r = s.get("return_pct")
        col = "var(--green)" if (r or 0) >= 0 else "var(--red)"
        sold_rows += (f'<tr><td><a href="https://finance.yahoo.co.jp/quote/{s["code"]}.T" target="_blank">{s["code"]}</a></td>'
                      f'<td>{s.get("name","")}</td><td class="num muted">{s.get("date","")}</td>'
                      f'<td class="num">{_yen(s.get("pl"))}</td>'
                      f'<td class="num" style="color:{col};font-weight:700">{("—" if r is None else f"{r:+.1f}%")}</td></tr>')
    sold_block = (f'''<h2 style="font-size:.95rem;margin:16px 0 8px;">入れ替えで売却した銘柄（{len(sold)}銘柄・利益/損失を確定）</h2>
  <div class="card tablebox"><table><thead><tr><th>コード</th><th>社名</th><th class="num">売却日</th><th class="num">確定損益</th><th class="num">騰落</th></tr></thead><tbody>{sold_rows}</tbody></table></div>'''
                  if sold else "")
    rebal_txt = ("・".join(rebal_days) + " に入れ替え") if rebal_days else "まだ入れ替えなし"

    explain_html = f"""
  <h2>📖 この結果になった理由（内訳）</h2>
  <div class="card" style="padding:14px 16px;line-height:1.75;">
    <b>ひとことで言うと：</b>{buy_day} に「割安 × 財務の質」で選んだ銘柄を<b>100株（1単元）単位</b>で、1銘柄あたり約10万円を目安に購入（計{pf.get('n_holdings')}銘柄・端数は現金）。
    日経平均が下がる相場でも、相対的に強い割安・好財務の銘柄を持ち続け、ルール通り月1回入れ替えた結果、
    <b>¥{cap:,} が ¥{final_v:,}（{pf.get('total_return_pct',0):+.2f}%）</b>になりました。
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0;">
    <div class="card" style="padding:12px 14px;"><div style="font-size:1.05rem;">🛒 ①買う（{buy_day}）</div><div class="muted" style="font-size:.82rem;margin-top:4px;line-height:1.6;">割安スコアで「厳選（警告なし）」かつ ROE 8%以上を、質の高い順に。<b>100株単位</b>で1銘柄あたり約10万円を目安に購入し、100株で予算を超える高株価銘柄はスキップ。最大10銘柄、余りは現金。</div></div>
    <div class="card" style="padding:12px 14px;"><div style="font-size:1.05rem;">🔄 ②入れ替える（{rebal_txt}）</div><div class="muted" style="font-size:.82rem;margin-top:4px;line-height:1.6;">約1か月ごとに、上位ランクから外れた銘柄を売り、新しい上位銘柄へ乗せ替え。‑25%の暴落ストップと最長12か月ルールも常時作動。</div></div>
    <div class="card" style="padding:12px 14px;"><div style="font-size:1.05rem;">📊 ③今の状態</div><div class="muted" style="font-size:.82rem;margin-top:4px;line-height:1.6;">10銘柄を保有中（現金¥{pf.get('cash',0):,}）。市場は{('%+.2f%%'%pf.get('bench_return_pct')) if pf.get('bench_return_pct') is not None else '—'}下げたが、選んだ銘柄群は相対的に上回った。</div></div>
  </div>
  <h2 style="font-size:.95rem;margin:16px 0 8px;">お金の増え方（{total_pl:+,}円の内訳）</h2>
  <div class="card tablebox"><table><tbody>
    <tr><td>実現利益（入れ替えで売却して<b>確定</b>した分）</td><td class="num">{_yen(realized)}</td></tr>
    <tr><td>含み損益（いま保有中の10銘柄の評価損益）</td><td class="num">{_yen(unrealized)}</td></tr>
    <tr><td>売買コスト・現金分（手数料0.2%など）</td><td class="num">{_yen(cost_etc)}</td></tr>
    <tr style="border-top:2px solid var(--line);font-weight:700;"><td>合計（¥{cap:,} → ¥{final_v:,}）</td><td class="num">{_yen(total_pl)}</td></tr>
  </tbody></table></div>
  <div class="card" style="padding:12px 16px;margin-top:10px;line-height:1.7;">
    <b>なぜ市場に勝てたのか：</b>同じ¥{cap:,}を日経平均に入れていたら <b>¥{bench_v:,}（{('%+.2f%%'%pf.get('bench_return_pct')) if pf.get('bench_return_pct') is not None else '—'}）</b>。
    下落局面でも割安・好財務の銘柄は下げにくく、一部は上昇したため、市場に対して <b class="gold">{('%+.2f%%'%pf.get('excess_pct')) if pf.get('excess_pct') is not None else '—'}</b> の差がつきました。
    <span class="muted" style="font-size:.8rem;">※単一期間・短期間の結果であり、今後も続く保証はありません。</span>
  </div>
  {sold_block}
"""

    # --- 🤖 本日のアクション（bot） ---
    acts = pf.get("today_actions", [])
    if acts:
        arows = ""
        for t in acts:
            if t.get("action") == "BUY":
                arows += (f'<li style="color:var(--green);"><b>🟢 買い</b>：{t.get("name","")}（{t["code"]}）を <b>{t.get("shares","?"):,}株</b>'
                          f'（約¥{t.get("amount",0):,}）</li>')
            else:
                rp = t.get("return_pct")
                arows += (f'<li style="color:var(--red);"><b>🔴 売り</b>：{t.get("name","")}（{t["code"]}）を <b>{t.get("shares","?"):,}株</b>'
                          f'（{"" if rp is None else f"{rp:+.1f}% ・"}{t.get("reason","")}）</li>')
        action_body = f'<div style="font-weight:700;margin-bottom:6px;">本日の推奨アクション（{len(acts)}件）</div><ul style="margin:0;padding-left:1.2em;line-height:1.9;">{arows}</ul>'
        action_border = "var(--gold)"
    else:
        action_body = '<div style="font-weight:700;">✅ 本日のアクションなし（保有を継続）</div>'
        action_border = "var(--line)"
    action_html = f"""
  <div class="card" style="padding:12px 16px;border-left:4px solid {action_border};">
    {action_body}
    <div class="muted" style="font-size:.78rem;margin-top:8px;line-height:1.6;">
      次回リバランスの目安：<b>{pf.get('next_rebalance_est','—')}</b>（月1回）／ 適用ルール：{pf.get('rule_version','—')}<br>
      ※これは仮想（ペーパー）運用の推奨です。発注はご自身で行ってください（自動発注はしません）。投資助言ではありません。
    </div>
  </div>
"""

    portfolio_section_html = f"""
  <h2>💰 ¥{cap:,} ルール運用シミュレーション</h2>
  <p class="disclaimer">ルール: {pf.get('rule','')}。{pf.get('start_date','')}起点で遡及＋以降フォワード、購入金額は実株価・評価は分割配当調整済のトータルリターン基準。<b>ペーパー（仮想）運用で実発注はありません。投資助言でもありません。</b>日本株の原則どおり100株（1単元）単位で購入し、端数は現金で保有＝実際に発注できる形。買い候補は履歴から再現可能な近似（厳選×ROE）。</p>
{action_html}
  <div class="kpis">
    <div class="kpi"><div class="n {'green' if tr >= 0 else ''}">¥{pf.get('final_value',cap):,}</div><div class="l">現在の資産（開始¥{cap:,}）</div></div>
    <div class="kpi"><div class="n {'green' if tr >= 0 else ''}">{tr:+.2f}%</div><div class="l">トータルリターン</div></div>
    <div class="kpi"><div class="n" style="color:var(--red)">{('—' if br is None else f'{br:+.2f}%')}</div><div class="l">日経（同額）</div></div>
    <div class="kpi"><div class="n gold">{('—' if exc is None else f'{exc:+.2f}%')}</div><div class="l">超過リターン</div></div>
    <div class="kpi"><div class="n">{pf.get('max_drawdown_pct')}%</div><div class="l">最大ドローダウン</div></div>
    <div class="kpi"><div class="n">{pf.get('n_holdings')}<span style="font-size:.9rem">銘柄</span></div><div class="l">現在の保有（現金¥{pf.get('cash',0):,}）</div></div>
  </div>
  <div class="card" style="padding:10px 14px;"><div style="display:flex;gap:14px;font-size:.72rem;color:var(--muted);margin-bottom:4px;"><span><span style="color:#3fb27f">━</span> ポートフォリオ</span><span><span style="color:#8b98a5">━</span> 日経(同額)</span><span>‑‑‑ 開始¥{cap:,}</span></div>{svg}</div>
  <h2 style="font-size:.95rem;margin:16px 0 8px;">現在の保有（{pf.get('n_holdings')}銘柄・すべて100株単位で購入）</h2>
  <div class="card tablebox"><table><thead><tr><th>コード</th><th>社名</th><th class="num">株数</th><th class="num">購入日</th><th class="num">投資額</th><th class="num">評価額</th><th class="num">損益</th></tr></thead><tbody>{hold_rows}</tbody></table></div>
{explain_html}
"""
else:
    portfolio_section_html = ""


def chips(items, cls):
    if not items:
        return '<span class="muted">なし</span>'
    return "".join(f'<span class="chip {cls}">{c} {n}</span>' for c, n in items[:30])

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日本株 割安監視ダッシュボード｜{data["date"]}</title>
<style>
  :root{{--bg:#0f1419;--card:#1a2129;--line:#2a3441;--ink:#e6edf3;--muted:#8b98a5;
    --green:#3fb27f;--red:#e5534b;--gold:#d4a23c;--blue:#539bf5;
    --sans:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,sans-serif;}}
  *{{margin:0;padding:0;box-sizing:border-box;}}
  body{{font-family:var(--sans);background:var(--bg);color:var(--ink);line-height:1.65;padding:24px 16px 60px;}}
  .wrap{{max-width:1180px;margin:0 auto;}}
  h1{{font-size:1.45rem;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}}
  h1 .date{{font-size:.85rem;color:var(--muted);font-weight:400;}}
  .disclaimer{{font-size:.74rem;color:var(--muted);margin:6px 0 18px;}}
  .kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:20px;}}
  .kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;}}
  .kpi .n{{font-size:1.6rem;font-weight:700;}}
  .kpi .l{{font-size:.74rem;color:var(--muted);}}
  .kpi .n.green{{color:var(--green);}} .kpi .n.gold{{color:var(--gold);}}
  h2{{font-size:1.05rem;margin:26px 0 10px;color:var(--gold);}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;}}
  table{{width:100%;border-collapse:collapse;font-size:.83rem;white-space:nowrap;}}
  th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;}}
  th{{color:var(--muted);font-size:.72rem;cursor:pointer;user-select:none;position:sticky;top:0;background:var(--card);}}
  th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){{text-align:left;}}
  td:nth-child(3) a{{white-space:nowrap;}}
  tr:hover td{{background:#202a35;}}
  .score{{font-weight:700;color:var(--green);}}
  .flag{{color:var(--red);font-size:.74rem;}}
  .chip{{display:inline-block;background:#22303c;border:1px solid var(--line);border-radius:999px;
    padding:2px 10px;font-size:.76rem;margin:2px;}}
  .chip.in{{border-color:var(--green);color:var(--green);}}
  .chip.out{{border-color:var(--red);color:var(--red);}}
  .muted{{color:var(--muted);font-size:.8rem;}}
  .controls{{display:flex;gap:10px;margin:10px 0;flex-wrap:wrap;}}
  input,select{{background:#0f1419;border:1px solid var(--line);color:var(--ink);border-radius:8px;
    padding:8px 12px;font-size:.85rem;font-family:inherit;}}
  .tablebox{{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:10px;}}
  a{{color:var(--blue);text-decoration:none;}}
  .meth{{font-size:.78rem;color:var(--muted);line-height:1.9;}}
  .meth b{{color:var(--ink);}}
</style>
</head>
<body>
<div class="wrap">
  <h1>📉 日本株 割安監視ダッシュボード <span class="date">更新: {data["generated_at"]}（東証プライム {data["universe"]}銘柄）</span></h1>
  <p class="disclaimer">※公開データ（Yahoo Finance）に基づく機械的なスクリーニングです。投資助言ではありません。売買はご自身の判断・責任で行ってください。指標は取得時点のもので、決算直後などはデータが古い場合があります。</p>

  <div class="kpis">
    <div class="kpi"><div class="n">{data["fetched"]}</div><div class="l">取得銘柄 / {data["universe"]}</div></div>
    <div class="kpi"><div class="n gold">{len(under)}</div><div class="l">割安判定（スコア{UNDER}+）</div></div>
    <div class="kpi"><div class="n green">{len(strict)}</div><div class="l">厳選（{STRICT}+・警告なし）</div></div>
    {f'<div class="kpi"><div class="n green">{sp_count}</div><div class="l">S+ランク（決算良好）</div></div>' if has_deep else ''}
    <div class="kpi"><div class="n green">+{len(new_in)}</div><div class="l">新規イン（前回比）</div></div>
    <div class="kpi"><div class="n" style="color:var(--red)">-{len(dropped)}</div><div class="l">アウト（前回比）</div></div>
  </div>
{portfolio_section_html}
{track_section_html}
{analysis_section_html}
  {'''<h2>🏆 Sランク厳選（割安 × 決算の質）</h2>
  <p class="disclaimer">スナップショットの割安スコアに、複数年決算（連続黒字・営業利益率・営業CF・配当の持続性・ネットキャッシュ）を加味して再選別。<b>S+＝決算良好で割安・警告ゼロ</b>、S＝概ね良好（軽微な懸念1つまで）。「安いだけ」のバリュートラップを除外した最優先リストです。</p>
  <div class="card tablebox"><table id="t-splus"></table></div>
  <p class="muted" style="margin:8px 0 0;">▼ Sランク（次点）</p>
  <div class="card tablebox" style="margin-top:6px;"><table id="t-srank"></table></div>''' if has_deep else ''}

  <h2>🔄 前回からの変化</h2>
  <div class="card">
    <div><b style="color:var(--green);font-size:.85rem;">新規イン</b>　{chips(new_in, "in")}</div>
    <div style="margin-top:8px;"><b style="color:var(--red);font-size:.85rem;">アウト</b>　{chips(dropped, "out")}</div>
  </div>

  <h2>⭐ 厳選割安リスト（スコア{STRICT}以上・警告フラグなし）</h2>
  <div class="card tablebox"><table id="t-strict"></table></div>

  <h2>⚠️ 高スコアだが要注意（バリュートラップ候補）</h2>
  <div class="card tablebox"><table id="t-caution"></table></div>

  <h2>🔍 全銘柄ビュー（検索・並べ替え可）</h2>
  <div class="controls">
    <input id="q" placeholder="コード・社名で検索" style="flex:1;min-width:200px;">
    <select id="sec"><option value="">全業種</option></select>
    <select id="minScore">
      <option value="0">スコア指定なし</option>
      <option value="40">40以上</option>
      <option value="60" selected>60以上（割安）</option>
      <option value="70">70以上</option>
    </select>
  </div>
  <div class="card tablebox"><table id="t-all"></table></div>

  <h2>📐 スコアの内訳（最大100点）</h2>
  <div class="card meth">
    <b>PBR</b>（最大25）: 0.6未満25 / 0.8未満20 / 1.0未満15 / 1.3未満8
    <b>PER</b>（最大25）: 8未満25 / 10未満20 / 12未満15 / 15未満8<br>
    <b>配当利回り</b>（最大20）: 4%+20 / 3%+15 / 2%+8
    <b>ROE</b>（最大15）: 10%+15 / 8%+12 / 5%+6
    <b>52週下値圏</b>（最大10）: 位置30%以下10 / 45%以下6
    <b>財務</b>（最大5）: D/E 50%未満5 / 100%未満3<br>
    <b>警告フラグ</b>: 赤字 / 利益急減 / 売上減 / 配当過大（減配リスク） / 低PBR×低ROE（万年割安） — 「安いだけの危険な株」を検出
  </div>
</div>

<script>
const DATA = {payload_js};
const UNDER = {UNDER}, STRICT = {STRICT};
const fmt = (v, d=1) => v==null ? "—" : Number(v).toLocaleString("ja-JP", {{maximumFractionDigits:d, minimumFractionDigits:0}});
const oku = v => v==null ? "—" : (v/1e8).toLocaleString("ja-JP", {{maximumFractionDigits:0}}) + "億";
const COLS = [
  ["code","コード"],["name","社名"],["chart","チャート"],["sector","業種"],["price","株価"],
  ["per","PER"],["pbr","PBR"],["yield","配当%"],["roe","ROE%"],
  ["pos52","52週位置"],["mcap","時価総額"],["score","スコア"],["flags","警告"]
];
function cell(r, k) {{
  if (k==="code") return `<a href="https://finance.yahoo.co.jp/quote/${{r.code}}.T" target="_blank">${{r.code}}</a>`;
  if (k==="name") return r.name;
  if (k==="chart") return `<a href="https://kabutan.jp/stock/chart?code=${{r.code}}" target="_blank">株探</a> · `
    + `<a href="https://www.tradingview.com/chart/?symbol=TSE%3A${{r.code}}" target="_blank">TV</a> · `
    + `<a href="https://finance.yahoo.co.jp/quote/${{r.code}}.T/chart" target="_blank">Y!</a>`;
  if (k==="sector") return `<span class="muted">${{r.sector||""}}</span>`;
  if (k==="price") return fmt(r.price, 1);
  if (k==="per") return fmt(r.per, 1);
  if (k==="pbr") return fmt(r.pbr, 2);
  if (k==="yield") return fmt(r.yield, 2);
  if (k==="roe") return fmt(r.roe, 1);
  if (k==="pos52") return r.pos52==null ? "—" : Math.round(r.pos52*100)+"%";
  if (k==="mcap") return oku(r.mcap);
  if (k==="score") return `<span class="score">${{r.score}}</span>`;
  if (k==="flags") return `<span class="flag">${{(r.flags||[]).join(" / ")}}</span>`;
}}
function render(el, rows) {{
  let h = "<thead><tr>" + COLS.map(([k,l],i)=>`<th data-k="${{k}}">${{l}}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) h += "<tr>" + COLS.map(([k])=>`<td>${{cell(r,k)}}</td>`).join("") + "</tr>";
  el.innerHTML = h + "</tbody>";
  el.querySelectorAll("th").forEach(th => th.onclick = () => {{
    const k = th.dataset.k;
    const dir = th.dataset.dir === "asc" ? -1 : 1;
    th.dataset.dir = dir === 1 ? "asc" : "desc";
    rows.sort((a,b) => {{
      const x=a[k], y=b[k];
      if (x==null) return 1; if (y==null) return -1;
      return (x<y?-1:x>y?1:0) * dir;
    }});
    render(el, rows);
  }});
}}
const strictRows = DATA.filter(r => r.score>=STRICT && (!r.flags||!r.flags.length));
const cautionRows = DATA.filter(r => r.score>=UNDER && r.flags && r.flags.length);
render(document.getElementById("t-strict"), strictRows);
render(document.getElementById("t-caution"), cautionRows);

const secSel = document.getElementById("sec");
[...new Set(DATA.map(r=>r.sector).filter(Boolean))].sort().forEach(s => {{
  const o=document.createElement("option"); o.value=o.textContent=s; secSel.appendChild(o);
}});
function applyAll() {{
  const q = document.getElementById("q").value.trim().toLowerCase();
  const sec = secSel.value;
  const ms = +document.getElementById("minScore").value;
  const rows = DATA.filter(r =>
    r.score>=ms && (!sec || r.sector===sec) &&
    (!q || r.code.toLowerCase().includes(q) || (r.name||"").toLowerCase().includes(q)));
  render(document.getElementById("t-all"), rows.slice(0, 500));
}}
["q","sec","minScore"].forEach(id => document.getElementById(id).addEventListener("input", applyAll));
applyAll();

// ===== Sランク厳選（決算深掘り） =====
const DEEP = {deep_js};
const NOTES = {notes_js};
const DCOLS = [["code","コード"],["name","社名"],["chart","チャート"],["tier","ﾗﾝｸ"],["verdict","ニュース検証"],["total","総合"],
  ["pbr","PBR"],["per","PER"],["yield","配当%"],["consec_profit","連続黒字"],
  ["op_margin","営業益率"],["net_cash_ratio","純現金/時価"],["roe","ROE%"],["gates","懸念"]];
function dcell(r,k){{
  if(k==="code") return `<a href="https://finance.yahoo.co.jp/quote/${{r.code}}.T" target="_blank">${{r.code}}</a>`;
  if(k==="name") return r.name;
  if(k==="chart") return `<a href="https://kabutan.jp/stock/chart?code=${{r.code}}" target="_blank">株探</a> · `
    +`<a href="https://www.tradingview.com/chart/?symbol=TSE%3A${{r.code}}" target="_blank">TV</a> · `
    +`<a href="https://finance.yahoo.co.jp/quote/${{r.code}}.T/chart" target="_blank">Y!</a>`;
  if(k==="tier") return `<b style="color:${{r.tier==='S+'?'#3fb27f':'#d4a23c'}}">${{r.tier}}</b>`;
  if(k==="verdict"){{
    const n=NOTES[r.code];
    if(!n) return '<span class="muted">—</span>';
    const col=n.verdict==="妙味あり"?"#3fb27f":n.verdict==="トラップ懸念"?"#e5534b":"#d4a23c";
    return `<b style="color:${{col}}">${{n.verdict||""}}</b>`
      +`<br><span class="muted" style="font-size:.7rem;white-space:normal;display:inline-block;max-width:240px">${{n.take||""}}</span>`;
  }}
  if(k==="total") return `<span class="score">${{r.total}}</span>`
    +`<span class="muted" style="font-size:.7rem"> (割安${{r.base}}+質${{r.quality}})</span>`;
  if(k==="pbr") return fmt(r.pbr,2);
  if(k==="per") return fmt(r.per,1);
  if(k==="yield") return fmt(r.yield,1);
  if(k==="consec_profit") return r.consec_profit==null?"—":r.consec_profit+"年";
  if(k==="op_margin") return r.op_margin==null?"—":Number(r.op_margin).toFixed(1)+"%";
  if(k==="net_cash_ratio") return r.net_cash_ratio==null?"—":Math.round(r.net_cash_ratio*100)+"%";
  if(k==="roe") return fmt(r.roe,1);
  if(k==="gates") return `<span class="flag">${{(r.gates||[]).join(" / ")}}</span>`;
}}
function renderDeep(el,rows){{
  if(!el) return;
  let h="<thead><tr>"+DCOLS.map(([k,l])=>`<th>${{l}}</th>`).join("")+"</tr></thead><tbody>";
  for(const r of rows) h+="<tr>"+DCOLS.map(([k])=>`<td>${{dcell(r,k)}}</td>`).join("")+"</tr>";
  el.innerHTML=h+"</tbody>";
}}
if(DEEP.length){{
  renderDeep(document.getElementById("t-splus"), DEEP.filter(r=>r.tier==="S+"));
  renderDeep(document.getElementById("t-srank"), DEEP.filter(r=>r.tier==="S"));
}}

// ===== 割安判定の追跡（成績） =====
const TRACK = {track_js};
if (TRACK && TRACK.signals) {{
  const TCOLS = [["code","コード"],["name","社名"],["chart","ﾁｬｰﾄ"],["entry_date","判定日"],
    ["entry_score","判定ｽｺｱ"],["entry_price","判定時株価"],["current_price","現在株価"],
    ["return_pct","リターン"],["excess_pct","日経比"],["days","経過日"],["status","状態"]];
  const tnum = (v,d=1)=> v==null?"—":Number(v).toLocaleString("ja-JP",{{maximumFractionDigits:d}});
  const tpct = v => v==null? '<span class="muted">—</span>'
    : `<b style="color:${{v>=0?'var(--green)':'var(--red)'}}">${{v>=0?'+':''}}${{v.toFixed(1)}}%</b>`;
  function tcell(r,k){{
    if(k==="code") return `<a href="https://finance.yahoo.co.jp/quote/${{r.code}}.T" target="_blank">${{r.code}}</a>`;
    if(k==="name") return (r.name||"") + (r.split_note?' <span class="muted" style="font-size:.65rem">(分割補正)</span>':'');
    if(k==="chart") return `<a href="https://kabutan.jp/stock/chart?code=${{r.code}}" target="_blank">株探</a> · <a href="https://www.tradingview.com/chart/?symbol=TSE%3A${{r.code}}" target="_blank">TV</a>`;
    if(k==="entry_date") return `<span class="muted">${{r.entry_date}}</span>`;
    if(k==="entry_score") return `<span class="score">${{r.entry_score}}</span>`;
    if(k==="entry_price") return tnum(r.entry_price,1);
    if(k==="current_price") return tnum(r.current_price,1);
    if(k==="return_pct") return tpct(r.return_pct);
    if(k==="excess_pct") return tpct(r.excess_pct);
    if(k==="days") return r.days;
    if(k==="status") return r.still_uv? '<span class="chip in">割安継続</span>'
      : (r.in_universe? '<span class="muted">対象外</span>':'<span class="muted">除外</span>');
  }}
  function trender(rows){{
    const el=document.getElementById("t-track");
    let h="<thead><tr>"+TCOLS.map(([k,l])=>`<th>${{l}}</th>`).join("")+"</tr></thead><tbody>";
    for(const r of rows) h+="<tr>"+TCOLS.map(([k])=>`<td>${{tcell(r,k)}}</td>`).join("")+"</tr>";
    el.innerHTML=h+"</tbody>";
  }}
  function tapply(){{
    const q=document.getElementById("tq").value.trim().toLowerCase();
    const sort=document.getElementById("tsort").value;
    const strictOnly=document.getElementById("tstrict").checked;
    let rows=TRACK.signals.filter(r=> r.return_pct!=null
      && (!strictOnly || r.was_strict)
      && (!q || r.code.toLowerCase().includes(q) || (r.name||"").toLowerCase().includes(q)));
    const cmp={{
      return_desc:(a,b)=>b.return_pct-a.return_pct,
      return_asc:(a,b)=>a.return_pct-b.return_pct,
      excess_desc:(a,b)=>(b.excess_pct??-1e9)-(a.excess_pct??-1e9),
      entry_score_desc:(a,b)=>b.entry_score-a.entry_score,
    }}[sort];
    rows.sort(cmp);
    trender(rows.slice(0,500));
  }}
  ["tq","tsort","tstrict"].forEach(id=>document.getElementById(id).addEventListener("input",tapply));
  tapply();
}}
</script>
</body>
</html>"""

(ROOT / "index.html").write_text(html, encoding="utf-8")
print(f"index.html 生成: 割安{len(under)} / 厳選{len(strict)} / 要注意{len(caution)}")
