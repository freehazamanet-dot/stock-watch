# 日本株 割安監視システム（stock-watch）

東証プライム全銘柄（約1,600社）を毎日スキャンし、割安株を自動抽出・監視する。

## 仕組み
```
JPX公式銘柄リスト(data_j.xls)
   ↓ プライム市場で絞り込み
Yahoo Finance から指標取得（PER/PBR/配当/ROE/52週レンジ/財務）
   ↓ 割安スコア算出(0-100) ＋ バリュートラップ警告
index.html（ダッシュボード）＋ メール通知（ryota000666@nomady.biz）
   ↑ launchd で平日16:30に自動実行
```

## ファイル
- `screener.py` — スクリーニング本体（→ data/latest.json, latest.csv, history/日付.json）
- `make_dashboard.py` — index.html 生成（検索・並べ替え・新規イン/アウト表示）
- `notify.py` — メール送信（inv-dental-ad.com/demo/mail.php 経由）
- `run.sh` — 上記3つを順に実行（logs/run.log に記録）
- `com.fastgrow.stockwatch.plist` — launchd 設定（平日16:30）

## 初回セットアップ
```sh
cd ~/Documents/stock-watch
/opt/homebrew/bin/python3 -m venv venv
./venv/bin/pip install yfinance pandas xlrd lxml
chmod +x run.sh

# テスト（30銘柄だけ）
./venv/bin/python screener.py --limit 30
./venv/bin/python make_dashboard.py
open index.html

# 自動実行を登録
cp com.fastgrow.stockwatch.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.fastgrow.stockwatch.plist
```

## 日常の使い方
- 何もしなくてOK。平日16:30に自動更新され、メールが届く
- ダッシュボード: `open ~/Documents/stock-watch/index.html`
- 手動実行: `zsh ~/Documents/stock-watch/run.sh`（全銘柄で15〜40分）
- 停止: `launchctl unload ~/Library/LaunchAgents/com.fastgrow.stockwatch.plist`

## スコアの考え方（最大100点）
| 指標 | 配点 | 満点条件 |
|---|---|---|
| PBR | 25 | 0.6未満 |
| PER | 25 | 8未満 |
| 配当利回り | 20 | 4%以上 |
| ROE | 15 | 10%以上（※安くて稼げる会社か） |
| 52週下値圏 | 10 | 安値から30%以内 |
| 財務(D/E) | 5 | 50%未満 |

- **割安判定**: スコア60以上
- **厳選リスト**: スコア70以上 かつ 警告フラグなし
- **警告フラグ（バリュートラップ検出）**: 赤字 / 利益急減(-30%超) / 売上減(-15%超) / 配当6.5%超（減配リスク） / 低PBR×低ROE（万年割安）

## 注意
- Yahoo Financeの無料データに基づく機械的な判定であり、**投資助言ではない**
- 決算直後は指標が古い場合がある。売買判断の前に最新の決算・適時開示を必ず確認すること
