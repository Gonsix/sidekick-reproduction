# Sidekick 図4(a)(b)(c) の独立再現実装

著者の Sidekick / quACK / Robin のソースは使用せず、論文のアルゴリズムから実装した研究用コードです。
QUIC/TLS/HTTP/3 は Cloudflare **quiche 0.17.2** を使用し、その上流ソースに別ブランチで変更を加えています。

- Ubuntu 作業場所: `/home/gonsix/sidekick-independent/quiche`
- ブランチ: `sidekick/independent-fig4-abc`
- 上流: https://github.com/cloudflare/quiche
- 基点: `a4ac85642eca40e45cc6e0cfd916d55b81537e2c`（tag 0.17.2）
- 論文: https://www.usenix.org/conference/nsdi24/presentation/yuan
- 著者実装を使った従来の実験: `/home/gonsix/sidekick_reproduction`。今回の実装・データとは分離しています。

今回実行した範囲・実測値・結果ファイルの案内は [RESULTS.md](RESULTS.md) を参照してください。

## すぐに実行する

現在の Ubuntu では専用コンテナ `sidekick-independent` が用意されています。
以下のコマンドは **Gonsix Ubuntu のターミナル**で実行します。

```sh
cd /home/gonsix/sidekick-independent/quiche

# ビルドとライブラリ全テスト
docker exec sidekick-independent bash -lc 'cd /work/quiche && cargo +1.100.0 test -p quiche --lib && cargo +1.100.0 build --release -p quiche --example sidekick_transfer --example sidekick_proxy --example sidekick_media'

# A/B/C の短い動作確認。出力先は毎回新しい名前にする。
docker exec sidekick-independent python3 reproduction/run.py \
  --suite smoke --figure all --output /work/results/my-smoke-001

# QUICで早期再送だけを有効にする。PACUBICとACK補助は無効。
docker exec sidekick-independent python3 reproduction/run.py \
  --figure b --modes e2e,early --trials 3 --size 10000000 \
  --output /work/results/early-only-001

# 同じ損失条件で、通常QUIC / 早期再送 / PACUBICを比較
docker exec sidekick-independent python3 reproduction/run.py \
  --figure b --modes e2e,early,pacubic --trials 3 --size 10000000 \
  --output /work/results/compare-b-001

# ACK削減: 通常QUICとSidekickで同じ受信側ACK間隔を使う
docker exec sidekick-independent python3 reproduction/run.py \
  --figure c --modes e2e,ack --ack-ms 320 --trials 3 --size 10000000 \
  --output /work/results/compare-c-001

# 生データの集計・PNG/SVG・CSV・日本語レポート
docker exec sidekick-independent python3 reproduction/analyze.py \
  /work/results/my-smoke-001 --standard-only \
  --output /work/results/my-smoke-001/plots
```

ホストの結果は `/home/gonsix/sidekick-independent/results/` に残ります。
成功済みデータを読み込んで「実行済み」にする処理はありません。
出力ディレクトリが存在する場合、ランナーは開始を拒否します。

## 3機能とマスタースイッチ

QUIC送信プログラムのオプションはすべて `on / off` です。
ライブラリの既定値は **Sidekick OFF・3機能すべてOFF** です。

| オプション | 作用 |
|---|---|
| `--sidekick on/off` | マスタースイッチ。OFFではRobin用ソケットを作らず、quACKによる輸送制御を行わない |
| `--early-retransmit on/off` | quACKから近側の欠損を検出し、QUICの元のフレームを再送キューに入れる。再送時は新しいQUICパケット番号を使う |
| `--pacubic on/off` | 近側で起きた輻輳イベントに、RTT比で調整したCUBICのβとCを使う。早期再送とは独立に切り替え可能 |
| `--ack-reduction on/off` | quACKで輻輳窓とアプリへの書き込み可能量を進め、間引かれた実ACKを待つ停止を減らす。信頼配送用フレームは保持する |

受信側の実際のACK間隔は、別の実験パラメータ `--ack-delay-ms`（ランナーでは `--ack-ms`）で指定します。
この間隔をE2E/Sidekickで揃えることで、ACK間引きに対するSidekickの補助効果を比較します。
`--ack-ms 0` は上流quicheの通常のACK生成です。
Sidekick OFFと通常ACKの組合せが通常のエンドツーエンドQUICです。
ACK間隔を指定したE2Eは「ACKを間引いたE2E」の比較条件になります。

ランナーの `--modes` は次の組合せを選択します。

| mode | Sidekick | 早期再送 | PACUBIC | ACK補助 |
|---|---|---|---|---|
| e2e | OFF | OFF | OFF | OFF |
| observe | ON | OFF | OFF | OFF |
| early | ON | ON | OFF | OFF |
| pacubic-only | ON | OFF | ON | OFF |
| ack | ON | OFF | OFF | ON |
| pacubic | ON | ON | ON | OFF |
| early-ack | ON | ON | OFF | ON |
| pacubic-ack | ON | OFF | ON | ON |
| all | ON | ON | ON | ON |
| off-requested-all | OFF | ON指定 | ON指定 | ON指定 |

最後の条件ではマスターOFFが優先され、3つの指定は輸送制御に影響しません。
`--suite ablation --figure b` はこの全条件を実行します。
UDPメディアの比較には `e2e / early` を使います。

ライブラリでは `Connection::configure_sidekick(Options, token)` で設定し、
接続中に `Options { enabled: false, .. }` を再設定して無効化できます。
進行中のQUIC/TLS接続・ストリーム・信頼配送バッファは維持します。
既に与えた代理ACKの会計は実ACKで精算し、二重の窓拡大を防ぎます。
PACUBICの利用時はCUBICを選択してください。
実験プログラムではCUBICを明示指定し、HyStart++を両条件で無効にしています。

## 実装の構成

| ファイル | 内容 |
|---|---|
| `quiche/src/sidekick.rs` | 有限体のpower sums、Newtonの恒等式、候補根評価、累積quACKの復号、Robin送信側状態 |
| `quiche/src/lib.rs` | 暗号化後のUDPデータグラムの識別、設定API、quACK受信、ACKタイマーと送信容量の更新 |
| `quiche/src/recovery/mod.rs` | 元の再送キューへの接続、代理ACKの会計、実ACKまでのフレーム保持 |
| `quiche/src/recovery/cubic.rs` | PACUBICのβ・Cと通常CUBICへの切り替え |
| `quiche/examples/sidekick_proxy.rs` | AF_PACKETによるIPv4/UDP観測、累積quACK、設定・リセット・送信クォータ |
| `quiche/examples/sidekick_transfer.rs` | TLS/QUIC上の実HTTP/3 POST。バイト数とSHA-256を双方で検証 |
| `quiche/examples/sidekick_media.rs` | AES-GCMで連番を隠したUDPメディア、NACK・早期再送・de-jitterバッファ |
| `reproduction/run.py` | 名前空間・veth・HTB/netem、プロセス監視、pcap、試行ごとの保存と後片付け |
| `reproduction/analyze.py` | 成功した実測データの集計、図4A/B/C形式のPNG/SVG、試行CSV |
| `reproduction/test_runner.py` | 故障時の失敗判定と名前空間・子プロセスの後片付け検証 |

プロキシはQUICの復号鍵を持たず、QUICパケット番号を読みません。
識別子には暗号化済みデータグラム末尾の4バイトを使い、素数
`4294967291` の有限体へ写像します。QUICのwire formatは変更しません。

quACKは `count / last identifier / t個のpower sums` を持つ累積表現です。
UDP制御ヘッダを含む独自のシリアライズは `36 + 4t` バイトです。
復号後は候補根数だけでなく **全power sums** を再検証します。
衝突、閾値超過、不整合、古いepochでは不確かな情報を輸送制御に使いません。
必要時にはepochをリセットし、元のエンドツーエンド再送を継続します。
QUICの欠損判定は3個の後続受信、メディアは論文のメディア実装に合わせて後続1個です。

PACUBICは近側RTT / E2E RTT を `r` として、
近側の損失で `β = 1 - r(1 - 0.7)`、`C = 0.4 / r^3` を使います。
近側RTTはquACK対象の最終送信時刻から推定し、プロキシ内の待ち時間を差し引きます。
遠側損失には通常CUBICの応答を残します。

## 図4のネットワークと測定

```text
Sender 10.200.1.2 --- Proxy 10.200.1.1 / 10.200.2.1 --- Receiver 10.200.2.2
```

| 図 | Sender–Proxy（片道・各送信方向） | Proxy–Receiver（片道・各送信方向） | 標準quACK |
|---|---|---|---|
| 4A | 1 ms / 100 Mbps / 3.6% loss | 25 ms / 10 Mbps / 0% loss | 2パケットごと、t=8 |
| 4B | 1 ms / 100 Mbps / 1% loss | 25 ms / 10 Mbps / 0% loss | 30 msごと、t=10 |
| 4C | 25 ms / 10 Mbps / 0% loss | 1 ms / 100 Mbps / 0% loss | 15 msごと、t=50 |

損失率は往復合計ではなく、対応するリンクの各送信方向の設定です。
`--multiplier 2/4` でquACK間隔と閾値を両方2倍/4倍にします。
`--loss` と `--far-loss` は追加検証用の上書きです。
各試行で実測ping、適用したqdisc、drop統計を保存します。

- **4A:** 240バイトのメディアデータを20 msごとに生成します。
  `jitter = 順番に再生可能になった時刻 - 最初の受信時刻`。
  重複で最初の受信時刻を上書きせず、0も全標本に含めます。
  連番・受信時刻・再生時刻・待機時間を `media.csv` に保存します。
  NACK再送はE2E/Sidekickの両方で有効です。末尾の欠損も回復を待ちます。
- **4B:** `goodput = 検証済みアプリデータのバイト数 × 8 / 秒数`。
  送信側の単調時計で、QUIC接続開始から検証済みHTTP/3応答を得るまでを測ります。
  Sidekickの初回設定時間はこの転送時間に含めません。
  HTTPリクエスト開始以後の時間も別に保存します。
- **4C:** 横軸は受信側quicheが実際に送った **ACKフレームを含むQUICパケット数**。
  ハンドシェイクのACKも含みます。quACKは別に数えます。
  これは無線の消費電力を直接測定した値ではありません。

分位点はfloor(p*N)、4Aは全成功試行の標本を連結します。
4Bは中央値と第1・第3四分位を表示し、4Cは各試行の点を表示します。
試行順序は固定seedでシャッフルしますが、netemの損失乱数はカーネル依存です。
同一損失列を保証するseedではありません。

## 保存物と失敗判定

各試行には `manifest.json / sender.log / receiver.log / result.json` があり、
Sidekick条件には `proxy.log`、標準では `near.pcap` と `tc-before/after.json` も残します。
pcapは96バイトのsnaplenなので、全アプリデータの保存ではありません。

終了コード、watchdog、受信バイト数、ハッシュ、メディア標本数、quACK有効受信数を検査します。
失敗は `manifest.json` にfailedとして残し、集計へ混入させません。
例外・中断時には自分の試行が作った子プロセスと名前空間だけを掃除します。
既存の著者実験や他のコンテナは停止しません。

`environment.json` は基点・ブランチ・Rust・kernel・Cargo.lockとソース/バイナリのSHA-256を保存します。
生データから `analyze.py` を再実行できます。
`--standard-only` は通常条件のE2E/early/PACUBIC/ACK補助に絞り、
遠側損失などの追加試験を同じ条件として合算しません。

## 論文との差と適用範囲

アルゴリズムと機能を独立に再現した実装です。著者実装のwire互換版ではありません。

1. Robinの制御は既知PEPへ明示設定します。固定128ビットtoken、epoch、設定確認、
   クォータと更新・resetを実装しています。透明PEPの自動発見とセッションIDローテーションは実装していません。
2. HTTP/3はquicheへ直接接続しています。libcurl/nginxは使用していません。
3. qdiscはHTB + netemです。著者実験のRED設定をそのまま再現したものではありません。
4. ACK間隔は両端に事前設定する実験APIです。ACK_FREQUENCY拡張の交渉は実装していません。
5. ACK削減条件では、全モードに同じ64 MBのQUIC flow-control上限を広告します。
   quACKで受信者の広告上限を無視する処理は行いません。輻輳窓・送信バッファ容量はquACKで進めます。
   本ランナーの転送量は最大50 MBです。小さな受信上限を強制した場合は元のflow controlに従います。
6. IPv4・1フロー・固定経路の実験用です。1 epochは最大100,000データグラム、
   tは1–200です。リセットは送信プログラムが最大3回試みます。
7. TLSは自己署名の実験用証明書を使います。メディアは試行ごとの鍵でAES-GCM暗号化します。
   この例のHTTPクライアントは証明書検証を無効にしています。
8. TCPの実split-PEPとの公平性実験、実Wi-Fiの電力測定、論文の数値一致は、この検証とは別です。

古い上流の任意依存 `boring 2.x` がyankされていたため、任意依存指定を4.xへ変更しています。
今回使うのは既定のvendored BoringSSLです。boring-crateを使う別featureは検証対象外です。
Rust 1.100.0でビルドし、解決結果をCargo.lockへ固定しています。

## 論文規模の測定を行う

```sh
docker exec sidekick-independent python3 reproduction/run.py \
  --suite paper --figure all --output /work/results/full-paper-001
```

計画は4Aが60秒×5試行×4条件、4Bが1/5/10/20/50 MB×20試行×4条件、
4Cが10 MB×ACK間隔6段階×5試行×4条件です。長時間かかります。
`--trials` で反復数を明示変更できます。
これは実行計画であり、同数の試行が既に実行済みであることを意味しません。
今回実行した範囲と数値は測定結果のREPORT.mdおよびcompleted.jsonを確認してください。

## 環境を作り直す

```sh
cd /home/gonsix/sidekick-independent/quiche
docker build -t sidekick-independent-env:rust-1.100 reproduction
docker run -d --name sidekick-independent-new --privileged \
  -v /home/gonsix/sidekick-independent:/work \
  -w /work/quiche sidekick-independent-env:rust-1.100 sleep infinity
docker exec sidekick-independent-new git config --global --add safe.directory /work/quiche
docker exec sidekick-independent-new bash reproduction/build.sh
```

ネットワーク管理権限はこの専用コンテナの中で使用します。
`--network host` は不要です。
`build.sh` は証明書を必要時だけ生成し、ロック済み依存でビルド・全ライブラリテストを行います。
