#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日次サマリーをメール送信（inv-dental-ad.com の既存 mail.php 経由）"""
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://inv-dental-ad.com/demo/mail.php"
TO_NAME = "株価監視システム"
TO_MAIL = "ryota000666@nomady.biz"

data = json.loads((ROOT / "data" / "latest.json").read_text(encoding="utf-8"))
UNDER, STRICT = data["undervalued_min"], data["strict_min"]
results = data["results"]
name_of = {r["code"]: r["name"] for r in results}
under = [r for r in results if r["score"] >= UNDER]
strict = [r for r in under if r["score"] >= STRICT and not r["flags"]]

# 決算深掘り（Sランク厳選）
deep_path = ROOT / "data" / "deep.json"
deep = json.loads(deep_path.read_text(encoding="utf-8")) if deep_path.exists() else {"results": []}
splus = [r for r in deep.get("results", []) if r["tier"] == "S+"]
srank = [r for r in deep.get("results", []) if r["tier"] == "S"]

lines = [
    f"日本株 割安スクリーニング {data['date']}",
    f"対象: 東証プライム {data['universe']}銘柄（取得 {data['fetched']} / 失敗 {data['failed']}）",
    f"割安(スコア{UNDER}+): {len(under)}銘柄 ／ 厳選({STRICT}+): {len(strict)}銘柄",
    f"決算良好S+ランク: {len(splus)}銘柄 ／ Sランク: {len(srank)}銘柄",
    "",
    f"■新規イン: " + (", ".join(f"{c} {name_of.get(c,'')}" for c in data.get("new_in", [])[:20]) or "なし"),
    f"■アウト: " + (", ".join(f"{c} {name_of.get(c,'')}" for c in data.get("dropped", [])[:20]) or "なし"),
    "",
    f"■S+ランク厳選（割安×決算良好・警告ゼロ）",
]
for r in (splus or strict[:20]):
    lines.append(
        f"{r['code']} {r['name']}  "
        + (f"総合{r['total']}(割安{r['base']}+質{r['quality']}) | 連黒{r.get('consec_profit','?')}年 | "
           if 'total' in r else f"score {r['score']} | ")
        + f"PBR {r['pbr']:.2f} | PER {r['per']:.1f} | 配当 {(r['yield'] or 0):.1f}%"
    )
lines += ["", "ダッシュボード: ~/Documents/stock-watch/index.html",
          "※機械的スクリーニングであり投資助言ではありません。"]

body = {
    "_demo": f"株式割安監視 {data['date']}",
    "お名前": TO_NAME,
    "メールアドレス": TO_MAIL,
    "サマリー": "\n".join(lines),
}
req = urllib.request.Request(ENDPOINT, data=urllib.parse.urlencode(body).encode())
with urllib.request.urlopen(req, timeout=30) as res:
    ok = res.status == 200
print(f"メール送信: {'OK' if ok else 'NG'}（{TO_MAIL} 宛）")
