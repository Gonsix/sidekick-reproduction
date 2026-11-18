# 測定データ

このフォルダには、実験時に保存した測定データのうち、
図・表の再生成と条件の確認に使うファイルを収録しています。

- validation-final/ は独立再現実装の44試行です。主図は通常条件35試行を使います。
- cubic-cause-audit/ はCUBICのrollbackを切り替えた追加6試行です。
- ack-cause-audit/ はACK間引き条件でキュー構成などを比較した追加12試行です。

各試行の結果、条件、キュー統計、校正値と、図4Aの受信側CSVを保存しています。
環境JSONにあるパスは測定当時の記録です。パケットキャプチャや端末ログはUbuntu側に保管しています。

Matplotlibを使える環境で、リポジトリのルートから図表を再生成できます。

    python3 reproduction/build_results_report.py --output results/rebuilt-report

通常の再実験には [実行手順](../README.md) を参照してください。
