#!/bin/zsh
# 日次実行: スクリーニング → ダッシュボード生成 → メール通知
cd "$(dirname "$0")"
mkdir -p logs
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') run start ====="
  ./venv/bin/python screener.py &&
  ./venv/bin/python deep_screener.py &&
  ./venv/bin/python tracker.py &&
  ./venv/bin/python analyze_signals.py &&
  ./venv/bin/python portfolio.py &&
  ./venv/bin/python make_dashboard.py &&
  ./venv/bin/python notify.py
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') run end (exit $?) ====="
} >> logs/run.log 2>&1
