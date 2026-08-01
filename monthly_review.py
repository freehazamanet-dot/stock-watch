#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月次レビュー: analysis.json / tracking.json から改善レビューを生成。
reports/YYYY-MM.md に保存し、同内容をメール送信（承認制の改善候補を提示）。
精度向上ループの「継続レビュー」に相当。ルール自体は自動変更しない（過去最適化回避）。
"""
import json
import sys
import datetime
import urllib.parse
import urllib.request
from pathlib import Path

DRY = "--dry" in sys.argv  # --dry でメール送信をスキップ（テスト用）

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://inv-dental-ad.com/demo/mail.php"
TO_MAIL = "ryota000666@nomady.biz"
PAGES = "https://freehazamanet-dot.github.io/stock-watch/"


def fnum(v, sign=True):
    if v is None:
        return "—"
    return (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def main():
    ap = ROOT / "data" / "analysis.json"
    tp = ROOT / "data" / "tracking.json"
    if not ap.exists() or not tp.exists():
        print("analysis.json / tracking.json が無い。先に日次パイプラインを回してください")
        return
    a = json.loads(ap.read_text(encoding="utf-8"))
    t = json.loads(tp.read_text(encoding="utf-8"))["summary"]
    ym = datetime.date.today().strftime("%Y-%m")
    ph = a.get("primary_horizon", {})
    dates = t.get("snapshot_dates", [])
    span = f"{dates[0]}〜{dates[-1]}" if dates else ""

    L = [f"# 割安スクリーニング 月次レビュー {ym}", "",
         f"- 母集団: スナップショット{t.get('n_snapshots')}点（{span}）／ 追跡{t.get('n_signals_total')}銘柄",
         f"- 目標: {a.get('target','')} ／ 主ホライズン: {ph.get('label','')}（n={ph.get('n')}, 信頼度{a.get('confidence','')}）",
         "", "## ホライズン別 成績（対日経・超過）"]
    for h in a.get("horizon_summary", []):
        if h.get("n"):
            L.append(f"- {h['horizon']}: n={h['n']}, 対市場勝率{h.get('win_rate_vs_mkt')}%, "
                     f"平均超過{fnum(h.get('avg_excess'))}（平均リターン{fnum(h.get('avg_return'))}）")
        else:
            L.append(f"- {h['horizon']}: 蓄積中")

    L += ["", "## どの指標が効いているか（signed IC: 正=期待どおり効果あり）"]
    for f in sorted(a.get("factor_ic", []), key=lambda x: -(x["signed_ic"] if x["signed_ic"] is not None else -9)):
        rel = "" if f.get("reliable") else "（標本不足で保留）"
        ic = "—" if f.get("signed_ic") is None else f"{f['signed_ic']:+.3f}"
        L.append(f"- {f['label']}: {ic}  (n={f.get('n')}){rel}")

    L += ["", "## 改善候補（承認制・反映前に必ず前向き検証）"]
    L += [f"- {x}" for x in a.get("findings", [])]
    L += ["", "## 注意（過信しないための前提）"]
    L += [f"- {x}" for x in a.get("caveats", [])]
    L += ["", f"ダッシュボード: {PAGES}",
          "※このレビューは自動生成の叩き台です。ルールの変更は承認＋前向き検証の上で行ってください。"]
    md = "\n".join(L)

    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / f"{ym}.md").write_text(md, encoding="utf-8")
    print(f"reports/{ym}.md 生成")

    if DRY:
        print("(--dry: メール送信スキップ)")
        return
    body = {"_demo": f"割安スクリーニング 月次レビュー {ym}",
            "お名前": "株価監視システム", "メールアドレス": TO_MAIL, "サマリー": md}
    try:
        req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode(body).encode())
        with urllib.request.urlopen(req, timeout=30) as res:
            print("メール送信:", "OK" if res.status == 200 else "NG")
    except Exception as e:
        print("メール送信スキップ:", e)


if __name__ == "__main__":
    main()
