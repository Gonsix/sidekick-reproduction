# 今回の実装・検証結果

実施場所は Gonsix Ubuntu、2026-11-17。
著者の公開データや既存の著者実装の測定値は、以下の独立実装の測定へ混ぜていません。

## 完了した検証

- quicheライブラリ: **462 tests passed**（独立実装の追加テストを含む）
- ランナーの故障注入: **3 tests passed**
- 最終のシナリオ検証: **44試行すべて成功**
- 新規Docker環境でも固定Rust・ロック済み依存から再ビルドし、462テストとACK削減の実通信に成功
- 集計では失敗データ除外と重複入力の二重計上防止をfixtureで検証
- 最終ソース再ビルド後の疎通: master OFF / early / PACUBIC の3条件に成功
- 3機能の8組合せ、観測のみ、マスターOFFの優先を検証
- 近側損失と遠側損失、quACK対象データの保持・実ACKでの解放、
  二重の窓拡大防止、接続中のマスターOFF、古いepoch拒否を検証
- 受信データのバイト数とSHA-256、メディアの全標本数を照合

44試行には機能確認・追加損失条件を含みます。
そのうち通常条件の35試行をメイン図へ使い、全44試行はdiagnosticsに分けて集計しています。
失敗を意図的に起こすランナーテストは、この44試行の集計対象ではありません。
論文と同数の反復試行を完了したという意味ではありません。

## 主要な実測値

図4Aは条件ごとに60秒・3,000標本、各1試行です。

| 条件 | de-jitter p99 (ms) |
|---|---:|
| Simple E2E | 64.083 |
| Sidekick: 2パケットごと、t=8 | 22.241 |
| Sidekick(2x): 4パケットごと、t=16 | 52.141 |
| Sidekick(4x): 8パケットごと、t=32 | 52.243 |

図4Bは10 MB・各2試行の中央値です。

| 条件 | goodput (Mbit/s) |
|---|---:|
| QUIC E2E | 8.072 |
| 早期再送のみ | 3.625 |
| 早期再送 + PACUBIC | 9.080 |

この環境では、E2Eも比較的高いgoodputに達しています。
早期再送だけではgoodputが下がり、PACUBICとの併用で改善しています。
論文の3.6倍という改善幅まで再現した結果ではありません。
上流quicheの版・輻輳制御の挙動・キュー設定・実行環境の差を含むため、
機能の検証と、論文本文の数値一致を区別してください。

図4Cは10 MB・各2試行の中央値です。ACK数はハンドシェイクを含むACK-bearing QUIC packetsです。

| 受信側ACK間隔 | 条件 | goodput (Mbit/s) | 受信側ACK数 |
|---|---|---:|---:|
| 通常 | QUIC E2E | 9.086 | 7,257 |
| 80 ms | QUIC E2E | 9.014 | 111 |
| 80 ms | Sidekick ACK補助 | 9.086 | 110 |
| 320 ms | QUIC E2E | 8.079 | 31 |
| 320 ms | Sidekick ACK補助 | 9.088 | 28 |

ACK数の削減とgoodput維持を確認できました。
ここで示す値はこの実装・ホスト・反復数での結果です。
CPU専用化や長時間の公平性検証は行っていません。
機能確認の初期部分では描画依存の導入も並行していました。
この結果を確定的な性能推定に使う場合は、専用状態で反復数を増やしてください。

## Ubuntu上の保存先

基準ディレクトリ: `/home/gonsix/sidekick-independent/`

- ソース: `quiche/`
- 実行手順・オプション・論文との差: `quiche/reproduction/README.md`
- 完了44試行: `results/validation-final/completed.json`
- 全生ログ・pcap・tc統計: `results/validation-final/000-...` などの試行フォルダ
- メイン図: `results/validation-final/plots/fig4a.png`、`fig4b.png`、`fig4c.png`（SVGもあり）
- メイン集計: `results/validation-final/plots/REPORT.md`、`trials.csv`、`summary.json`
- 全条件の集計: `results/validation-final/diagnostics/REPORT.md`、`trials.csv`
- 測定時の正確なコード: `results/validation-final/measured-source.tar.gz`
- 測定時の上流に対する差分: `results/validation-final/measured.patch`
- 環境・ソース/バイナリSHA-256: `results/validation-final/environment.json`
- ライブラリ全テストログ: `verified-tests.log`
- ランナー故障注入ログ: `runner-tests.log`
- 新規環境のログ: `fresh-environment.log`、疎通データ: `results/fresh-environment-smoke/`
- 再ビルド後の疎通: `results/final-source-smoke/`

測定後に無関係な上流ファイルの整形差分を除き、接続中の無効化の単体テストと手順を追加しています。
輸送制御アルゴリズムは測定時から変更していません。
測定時のスナップショットと最終ブランチの両方を保存し、後から対応を追えるようにしています。

## 再測定

早期再送だけを有効にする例:

```sh
docker exec sidekick-independent python3 reproduction/run.py \
  --figure b --modes e2e,early --trials 3 --size 10000000 \
  --output /work/results/my-early-only-001
```

3機能の組合せ:

```sh
docker exec sidekick-independent python3 reproduction/run.py \
  --suite ablation --figure b --trials 3 --size 10000000 \
  --output /work/results/my-ablation-001
```

長い論文規模の計画は `--suite paper --figure all` で開始できます。
手順と予定試行数はREADMEを参照してください。

## 図4Aの累積分布：50 / 100 / 150 / 200 ms以内

![Simple E2EとSidekickの図4A比較](../../results/validation-final/fig4a-comparison/fig4a_e2e_sidekick_cdf.png)

|条件|50 ms以内|100 ms以内|150 ms以内|200 ms以内|
|---|---:|---:|---:|---:|
|Simple E2E|95.70%|99.67%|100.00%|100.00%|
|Sidekick（2パケットごと・t=8）|99.90%|100.00%|100.00%|100.00%|

0 msも含む全3,000標本を分母にした累積割合です。各条件60秒・1試行です。
図の縦軸86〜100%は論文と同じ拡大表示で、86%以上の標本だけを再集計したものではありません。
作図スクリプトは `reproduction/plot_fig4a_comparison.py`。
PNG/SVG、閾値ごとのCSVと出典CSVのSHA-256を `results/validation-final/fig4a-comparison/` に保存しています。
