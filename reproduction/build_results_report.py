#!/usr/bin/env python3
from pathlib import Path
import json,csv,bisect,collections,statistics,hashlib,math,datetime
import argparse

parser = argparse.ArgumentParser(description="Regenerate the report from saved Sidekick measurements")
parser.add_argument("--results-root", type=Path, default=Path(__file__).resolve().parent / "data")
parser.add_argument("--output", type=Path, default=Path("results/detailed-report"))
args = parser.parse_args()
R = args.results_root
O = R / "validation-final"
D = args.output
A = D / "report_assets"

A.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(Path(p).read_text())
def save(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def q(v,p):return sorted(v)[min(len(v)-1,int(p*len(v)))]
def median(v):return statistics.median(v)
def f(x):return f'{x:.3f}'
def mdtable(h,rows):return '\n'.join(['|'+'|'.join(h)+'|','|'+'|'.join(['---']*len(h))+'|']+['|'+'|'.join(map(str,r))+'|' for r in rows])
def csvout(name,rows):
 with (A/name).open('w',newline='') as z:
  w=csv.DictWriter(z,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
original=read(O/'results.json')
primary=[d for d in original if d['case']['loss_override'] is None and d['case']['far_loss']==0 and d['case']['mode'] in {'a':['e2e','early'],'b':['e2e','early','pacubic'],'c':['e2e','ack']}[d['case']['figure']]]
assert len(original)==44 and len(primary)==35
def label(c,english=False):
 if c['mode']=='e2e':return 'Simple E2E' if c['figure']=='a' else 'QUIC E2E'
 if c['figure']=='b' and c['mode']=='early':return 'Early retransmission only' if english else '早期再送のみ'
 return 'Sidekick'+(f'({c["multiplier"]}x)' if c['multiplier']!=1 else '')
def style(c):
 if c['mode']=='e2e':return '#C98124','--','o'
 if c['figure']=='b' and c['mode']=='early':return '#50895D',':','^'
 return {1:('#2177A5','-','s'),2:('#78B8D6','-.','D'),4:('#7D8791',':','*')}[c['multiplier']]
ar=[];av={};bg=collections.defaultdict(list);cg=collections.defaultdict(list)
for d in primary:
 c=d['case']
 if c['figure']=='a':
  paths=[p for p in O.glob('*/result.json') if read(p)['case']==c];assert len(paths)==1
  p=paths[0].parent/'media.csv'
  with p.open() as z:vs=sorted(int(x['jitter_ns'])/1e6 for x in csv.DictReader(z))
  assert len(vs)==3000
  name=label(c);av[name]=(vs,c)
  ar.append({'condition':name,'multiplier':c['multiplier'],'mode':c['mode'],'trials':1,'samples':len(vs),
   'p95_ms':q(vs,.95),'p99_ms':q(vs,.99),'p999_ms':q(vs,.999),'max_ms':max(vs),
   **{f'within_{t}_ms_pct':100*bisect.bisect_right(vs,t)/len(vs) for t in [2.2,5,20,50,100,150,200]},
   'source_csv':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 elif c['figure']=='b':bg[(c['mode'],c['multiplier'],c['size'])].append(d)
 else:cg[(c['mode'],c['multiplier'],c['ack_ms'])].append(d)
ar.sort(key=lambda x:(x['mode']!='e2e',x['multiplier']))
br=[];cr=[]
for (mode,x,size),ds in sorted(bg.items(),key=lambda x:(x[0][2],{'e2e':0,'early':1,'pacubic':2}[x[0][0]],x[0][1])):
 vs=[d['sender']['goodput_mbps'] for d in ds]
 br.append({'condition':label(ds[0]['case']),'mode':mode,'multiplier':x,'size_MB':size/1e6,'trials':len(ds),
  'median_mbps':median(vs),'q25_mbps':q(vs,.25),'q75_mbps':q(vs,.75),'trial_mbps':vs})
for (mode,x,ack),ds in sorted(cg.items(),key=lambda x:(x[0][2],x[0][0]!='e2e',x[0][1])):
 vs=[d['sender']['goodput_mbps'] for d in ds];acks=[d['receiver']['ack_packets'] for d in ds]
 cr.append({'condition':label(ds[0]['case']),'mode':mode,'multiplier':x,'ack_interval_ms':ack,'trials':len(ds),
  'median_mbps':median(vs),'q25_mbps':q(vs,.25),'q75_mbps':q(vs,.75),'median_ack_packets':median(acks),
  'min_ack_packets':min(acks),'max_ack_packets':max(acks),'trial_mbps':vs,'trial_ack_packets':acks})
for name,rs in [('figure4a_summary.csv',ar),('figure4b_summary.csv',br),('figure4c_summary.csv',cr)]:csvout(name,rs)
tr=[]
for d in original:
 c=d['case'];s=d['sender'];r=d['receiver']
 tr.append({**c,'verified':s['verified'] and r['verified'],'goodput_mbps':s.get('goodput_mbps'),
  'sender_seconds':s.get('seconds'),'ack_packets':r.get('ack_packets'),'p99_ms':r.get('p99_ms'),
  'samples':r.get('samples'),'early_retransmissions':s.get('early_retransmissions',s.get('retrans_quack')),
  'valid_quacks':s['sidekick'].get('valid',0),'rejected_quacks':s['sidekick'].get('rejected',0),'resets':s['sidekick'].get('resets',0)})
csvout('all_original_trials.csv',tr)
ab=read(R/'cubic-cause-audit/results.json');ac=read(R/'ack-cause-audit/results.json')
gb=collections.defaultdict(list);gc=collections.defaultdict(list)
for d in ab:gb[(d['case']['mode'],d['audit_rollback_setting'])].append(d)
for d in ac:gc[(d['audit_queue'],d['audit_rollback_setting'],d['case']['mode'])].append(d)
abr=[];acr=[]
for (mode,roll),ds in sorted(gb.items(),key=lambda x:(x[0][0]!='e2e',x[0][1]!='on')):
 abr.append({'mode':mode,'rollback':roll,'trials':len(ds),'median_mbps':median([d['sender']['goodput_mbps'] for d in ds]),
  'trial_mbps':[d['sender']['goodput_mbps'] for d in ds],'restores':[d['sender']['audit_heuristic_rollbacks'] for d in ds]})
for key,ds in sorted(gc.items()):
 queue,roll,mode=key;drops=[]
 for p in (R/'ack-cause-audit').glob('*/result.json'):
  d=read(p)
  if (d['audit_queue'],d['audit_rollback_setting'],d['case']['mode'])!=key:continue
  n=0
  for entries in read(p.parent/'tc-after.json').values():
   for e in entries:
    if e.get('kind')==('red' if queue=='red' else 'netem'):n+=e.get('drops',0)
  drops.append(n)
 acr.append({'queue':queue,'rollback':roll,'mode':mode,'trials':len(ds),'median_mbps':median([d['sender']['goodput_mbps'] for d in ds]),
  'trial_mbps':[d['sender']['goodput_mbps'] for d in ds],'median_ack_packets':median([d['receiver']['ack_packets'] for d in ds]),
  'restores':[d['sender']['audit_heuristic_rollbacks'] for d in ds],'median_queue_drops':median(drops)})
csvout('cause_audit_b.csv',abr);csvout('cause_audit_c.csv',acr)
env=read(O/'environment.json')
data={'original_trials':44,'primary_trials':35,'by_figure':{x:sum(d['case']['figure']==x for d in original) for x in 'abc'},
 'a':ar,'b':br,'c':cr,'audit_b':abr,'audit_c':acr,
 'measured_environment':{k:v for k,v in env.items() if k not in ['cases','source_sha256']}}
save(A/'report_data.json',data)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10.5,'axes.spines.top':False,'axes.spines.right':False,
 'svg.fonttype':'none','axes.grid':True,'grid.alpha':.22})
def figsave(fig,name):
 fig.tight_layout()
 for ext in ['png','svg']:fig.savefig(A/f'{name}.{ext}',dpi=180,bbox_inches='tight')
 plt.close(fig)
fig,ax=plt.subplots(figsize=(8.5,4.8))
for name in [r['condition'] for r in ar]:
 vs,c=av[name];co,ls,m=style(c);xx=sorted(set(v for v in vs if v<=200))
 ax.step([0]+xx+[200],[0]+[100*bisect.bisect_right(vs,x)/len(vs) for x in xx]+[100*bisect.bisect_right(vs,200)/len(vs)],
  where='post',color=co,linestyle=ls,linewidth=2,label=label(c,True))
ax.set(xlim=(0,200),ylim=(86,100.3),xticks=[0,50,100,150,200],yticks=list(range(86,101,2)),xlabel='De-jitter latency (ms)',
 ylabel='Cumulative packets (%)',title='Figure 4A — encrypted UDP media')
ax.yaxis.set_major_formatter(PercentFormatter(100,decimals=0));ax.legend(loc='lower right',frameon=False)
figsave(fig,'fig4a_results')
fig,ax=plt.subplots(figsize=(8.5,4.8))
for mode,x in [('e2e',1),('early',1),('pacubic',1),('pacubic',2),('pacubic',4)]:
 rs=[r for r in br if r['mode']==mode and r['multiplier']==x]
 c=bg[(mode,x,int(rs[0]['size_MB']*1e6))][0]['case'];co,ls,m=style(c)
 ax.errorbar([r['size_MB'] for r in rs],[r['median_mbps'] for r in rs],
  yerr=[[r['median_mbps']-r['q25_mbps'] if r['trials']>1 else 0 for r in rs],
        [r['q75_mbps']-r['median_mbps'] if r['trials']>1 else 0 for r in rs]],
  color=co,linestyle=ls,marker=m,capsize=3,label=label(c,True),linewidth=1.7)
ax.set(xlim=(0,11),ylim=(0,10),xticks=[1,2,5,10],xlabel='Upload size (MB, decimal)',ylabel='Goodput (Mbit/s)',
 title='Figure 4B — HTTP/3 over QUIC, 1% near-path loss')
ax.legend(loc='lower right',fontsize=9,frameon=False);figsave(fig,'fig4b_results')
fig,axes=plt.subplots(1,2,figsize=(11.4,4.4),sharey=True)
for mode,x in [('e2e',1),('ack',1),('ack',2),('ack',4)]:
 rs=[r for r in cr if r['mode']==mode and r['multiplier']==x]
 c=cg[(mode,x,rs[0]['ack_interval_ms'])][0]['case'];co,ls,m=style(c)
 for ax in axes:ax.plot([r['median_ack_packets'] for r in rs],[r['median_mbps'] for r in rs],color=co,marker=m,
  linestyle=ls,label=label(c,True),linewidth=1.5)
axes[0].set(xlim=(0,8000),ylim=(0,10),xlabel='Receiver ACK-bearing packets',ylabel='Goodput (Mbit/s)',title='All ACK intervals')
axes[1].set(xlim=(0,250),xlabel='Receiver ACK-bearing packets',title='Zoom: 0–250 ACK-bearing packets')
axes[1].legend(loc='lower right',frameon=False,fontsize=9)
fig.suptitle('Figure 4C — 10 MB upload; measured medians',fontsize=12);figsave(fig,'fig4c_results')
lines=['# Sidekick 図4A・4B・4C 独立再現実験レポート','','## 1. 再現実験の結果','',
'本節は独立実装の実測結果を先に示します。著者の公開生データは本節のグラフ・表へ混ぜていません。',
'元の44試行のうち通常条件35試行をグラフに使用しています。残る9試行は機能の組合せ・遠側損失の追加検証です。原因調査の追加18試行は第3節で別に扱います。','',
'### 1.1 図4A：UDPメディアのde-jitter遅延','','![図4Aの再現結果](report_assets/fig4a_results.png)','',
'横軸は受信後に順番通り再生できるまでの待ち時間、縦軸はその時間以内に収まった全パケットの累積割合です。論文と同様に86〜100%を拡大し、分母には0 msの標本も含めます。','',
mdtable(['条件','50 ms以内','100 ms以内','150 ms以内','200 ms以内'],[[r['condition']]+[f'{r[f"within_{t}_ms_pct"]:.2f}%' for t in [50,100,150,200]] for r in ar]),'',
mdtable(['条件','試行数','標本数','p95 (ms)','p99 (ms)','p99.9 (ms)','最大 (ms)'],
 [[r['condition'],1,r['samples'],f(r['p95_ms']),f(r['p99_ms']),f(r['p999_ms']),f(r['max_ms'])] for r in ar]),'',
'各条件60秒・3,000標本・1試行です。標準Sidekickは2パケットごと/t=8、2xは4パケットごと/t=16、4xは8パケットごと/t=32です。',
'標準Sidekickのp99は64.083→22.241 msで約65.3%短縮しました。quACK間隔を2倍・4倍にすると約52 msとなり、改善が小さくなりました。',
'[図4A数値CSV](report_assets/figure4a_summary.csv) / [SVG](report_assets/fig4a_results.svg)','',
'### 1.2 図4B：QUIC/HTTP/3アップロードのgoodput','','![図4Bの再現結果](report_assets/fig4b_results.png)','',
'横軸はアプリデータ量、縦軸はgoodputです。点は試行中央値、誤差棒はQ1/Q3です。1試行の条件に誤差棒はありません。測定したサイズは1・2・10 MBで、20・50 MBの値は生成していません。','',
mdtable(['条件','サイズ (MB)','n','中央値 (Mbit/s)','Q1〜Q3 (Mbit/s)','quACK間隔 / t'],
 [[r['condition'],f'{r["size_MB"]:g}',r['trials'],f(r['median_mbps']),
 f(r['q25_mbps'])+'〜'+f(r['q75_mbps']) if r['trials']>1 else '—',
 'なし' if r['mode']=='e2e' else f'{30*r["multiplier"]} ms / {10*r["multiplier"]}'] for r in br]),'',
'10 MBではE2E 8.072に対し、早期再送＋PACUBICは9.080 Mbit/sで約1.125倍でした。早期再送だけでは3.625 Mbit/sでした。',
'1 MBのE2Eには大きな試行間のばらつきがあり、2試行の中央値だけで安定した性能は断定できません。',
'[図4B数値CSV](report_assets/figure4b_summary.csv) / [SVG](report_assets/fig4b_results.svg)','',
'### 1.3 図4C：ACK間引きとgoodput','','![図4Cの再現結果](report_assets/fig4c_results.png)','',
'横軸は受信側のACKフレームを含むQUICパケット数、縦軸はgoodputです。点は表と同じ試行中央値。右側はACK数0〜250の拡大で、左と同じデータです。','',
mdtable(['ACK間隔','条件','n','goodput中央値 (Mbit/s)','ACK数中央値','quACK間隔 / t'],
 [[('通常' if r['ack_interval_ms']==0 else str(r['ack_interval_ms'])+' ms'),r['condition'],r['trials'],f(r['median_mbps']),
 f'{r["median_ack_packets"]:g}','なし' if r['mode']=='e2e' else f'{15*r["multiplier"]} ms / {50*r["multiplier"]}'] for r in cr]),'',
'全条件10 MBです。ACK間隔0は上流quicheの通常のACK生成で、0 ms周期という意味ではありません。ACK数にはハンドシェイクのACKを含みます。',
'320 msではE2Eが8.079 Mbit/s・31個、標準Sidekickが9.088 Mbit/s・28個でした。ACK削減とgoodput維持は確認できますが、80 msのE2Eは既に約9 Mbit/sで、論文ほどの性能低下は出ていません。',
'[図4C数値CSV](report_assets/figure4c_summary.csv) / [SVG](report_assets/fig4c_results.svg)','',
'集計元の[全44試行の個別数値](report_assets/all_original_trials.csv)も保存しています。Q1/Q3はfloor(p×N)、中央値は通常の中央値です。n=2のQ1/Q3は観測値の下端/上端であり、信頼区間ではありません。']


PAPER='https://ginayuan.com/papers/nsdi24-sidekick.pdf'
AUTHOR='https://github.com/ygina/sidekick/blob/258aa8121a214b0738512e3c94df08ad7e40c02e/'
FORK='https://github.com/ygina/quiche/blob/362685a63a4ed8dac7bbe6614b4c4b38e992cb61/'
UP='https://github.com/cloudflare/quiche/blob/0.17.2/'
lines.append("""
## 2. 実験構成と論文との対応

### 2.1 共通ネットワーク

    Sender 10.200.1.2 ── Proxy 10.200.1.1 / 10.200.2.1 ── Receiver 10.200.2.2

Gonsix Ubuntu上の専用Dockerコンテナで、各試行に固有のLinuxネットワーク名前空間とvethを作りました。実Wi-Fiの測定ではなく、tcによるエミュレーションです。CPU専有化はしていません。

|対象|Sender–Proxy（片道）|Proxy–Receiver（片道）|標準quACK|
|---|---|---|---|
|4A|1 ms / 100 Mbit/s / 3.6%損失|25 ms / 10 Mbit/s / 0%損失|2パケットごと、t=8|
|4B|1 ms / 100 Mbit/s / 1%損失|25 ms / 10 Mbit/s / 0%損失|30 msごと、t=10|
|4C|25 ms / 10 Mbit/s / 0%損失|1 ms / 100 Mbit/s / 0%損失|15 msごと、t=50|

遅延・帯域・設定損失率・標準quACKは論文Table 5に合わせています。損失は各区間の両送信方向の設定で、往復全体の確率ではありません。設定上のE2E RTTは52 ms。2x/4xではquACK間隔とtを同じ倍率で増やします。

通常条件のキューはHTB→netem、netem limitは1,000パケットです。著者の公開実験はnetem→HTB→REDで、ここは一致していません。キュー順序・容量・早期drop方針の違いを、設定損失率が同じというだけで同一条件とは扱いません。

### 2.2 図4Aの構成と測定指標

- UDPで240バイトのメディアデータを20 msごと（50フレーム/秒）生成します。
- 連番はAES-GCMで暗号化します。プロキシは復号鍵や平文連番を使いません。
- E2E/Sidekickの両方で、受信側NACKによる再送を有効にします。SidekickはquACKによる早期再送を追加します。
- 受信側は、最初の受信時刻から順番通り再生可能になる時刻までの差を記録します。重複受信で最初の時刻を上書きせず、同じ連番は1回だけ集計します。
- このde-jitter遅延は、片道伝送遅延・RTT・RFC 3550のinterarrival jitter推定値とは異なります。
- 受信側の単調時計だけで時刻差を取るため、端点間の時計同期は使いません。
- 各条件60秒・1試行。0も含めた全3,000標本から分位点とCDFを求めます。

### 2.3 図4B/4Cの構成と機能スイッチ

Cloudflare quiche 0.17.2に、独立したquACK/Robin処理を追加しました。ベースの上流基点はa4ac85642eca40e45cc6e0cfd916d55b81537e2cです。著者のSidekick/quACK/Robin実装コードは組み込んでいません。

実際のQUIC/TLSとHTTP/3 POSTを使い、送受信バイト数とSHA-256一致を成功条件にします。CUBICを明示選択し、HyStart++は両条件で無効にしています。MBは10^6 byteで、10 MBは10,000,000 byteです。

goodputは、アプリデータのbit数を接続開始から検証済みHTTP/3応答までの秒数で割ります。初回Robin設定時間は転送時間に含めません。HTTPリクエスト開始後の時間も別に記録します。

|機能|今回の処理|
|---|---|
|SidekickマスターOFF|RobinソケットとquACKによる制御を無効にし、E2E QUICで通信|
|早期再送ON|近側の欠損をquACKで検出し、元のフレームをQUIC再送キューへ入れる。再送は新しいパケット番号|
|PACUBIC ON|近側損失について、近側RTT/E2E RTTの比に応じたCUBICのβ/Cを使う|
|ACK補助ON|quACKで輻輳窓・アプリへの書き込み可能量を進める。信頼配送用フレームの破棄は実ACKまで待つ|

4Bの標準Sidekickは「早期再送＋PACUBIC、ACK補助OFF」、4Cは「ACK補助ON、早期再送/PACUBIC OFF」です。受信側ACK間隔はE2E/Sidekickで同じ値を設定します。

接続/ストリームのflow-control上限は全モード64 MBです。quACKで受信者の広告上限を無視してフロー制御窓を進める処理は実装していません。この機能差と、大きな上限自体が性能差の原因であるかは、分けて評価します。

### 2.4 揃えた項目と揃っていない項目

|項目|対応状況|解釈上の注意|
|---|---|---|
|遅延・帯域・設定損失率|Table 5に合わせた|queue dropは設定損失率とは別に起こり得る|
|240 B / 20 msのメディア|合わせた|暗号化とアプリ実装は独立コード|
|quACK間隔・t・2x/4x|合わせた|制御メッセージのwire互換性はない|
|PACUBICのβ/Cの式|論文に沿って実装|RTT比はquACKから推定。著者コードには設定値を与える経路がある|
|QUICのベース|不一致|今回0.17.2。照合した著者公開forkはpackage version 0.18.0|
|E2E側のCUBIC補助処理|不一致|著者公開forkは小損失時rollback等を無効化。最初の44試行では上流動作を残した|
|キュー規律・容量|不一致|HTB→netem(1,000 packets) と netem→HTB→adaptive RED(4 BDP)|
|HTTP/3アプリ|不一致|今回はquicheへ直接接続、著者はlibcurl/nginx|
|ACK生成とACK delay値|不一致|同じ間隔値でも、ACK delayフィールドとタイマーの処理は同じではない|
|flow controlの代理前進|未実装|今回のACK補助は輻輳窓と送信容量が中心|
|PEP発見・制御|一部のみ|固定PEPへ明示設定、token/epoch/quota/resetあり。透明な自動発見とIDローテーションなし|
|反復数|不足|Aは各1試行、B/Cの主要点は各2試行。論文4Bは20試行中央値|
|ACK数の計数|不一致|今回ACK-bearing QUIC packets。著者描画は受信側NICのtx_packets差分|
|実Wi-Fi・消費電力・TCP split-PEP公平性|未検証|今回の機能検証から達成を主張しない|

照合した著者forkは公開アーティファクトが参照するコミットです。これが論文執筆時の全測定と完全に同一だったとまでは保証しません。
""")
lines += ['[論文Table 5・実装説明]('+PAPER+') / [著者ネットワーク設定]('+AUTHOR+'mininet/network.py) / [著者quicheの版]('+FORK+'quiche/Cargo.toml)','',
'## 3. 論文との違いと、その原因','','### 3.1 数値の比較','',
mdtable(['対象','論文本文','最初の独立再現実験','評価'],
[['4A p99','48.6→2.2 ms、約95%減','64.083→22.241 ms、約65.3%減','改善方向は一致。本文2.2 msは未再現'],
['4B 10 MB','Sidekick/E2Eが3.6倍','8.072→9.080 Mbit/s、約1.125倍','E2Eが高く出て改善比が小さい'],
['4C E2E・ACK 80 ms','約4.5 Mbit/s','9.014 Mbit/s','大きなE2E性能低下を未再現'],
['4C ACK削減','高goodputを保ちながら受信側パケットを削減','通常7,257個→Sidekick 320 msで28個・9.088 Mbit/s','傾向は確認。計数定義が違い96%減との直接比較はしない']]),'',
'[論文§6.2・図4]('+PAPER+')。本文、公開生データ、独立実装の3つを別の出典として扱います。','',
'### 3.2 図4A：20 ms周期とquACKのバッチ待ち','',
'2パケットごとのquACKでは、欠損の次の受信パケットでquACKが出る場合と、さらに1パケットを待つ場合があります。20 msの生成周期と近側RTT約2 msにより、後続パケットの待ちに約2 ms・約22 msの段差が生じることが考えられます。論文本文にも、欠損から再送まで20+2 msまたは40+2 msという説明があります。 [論文の時系列説明]('+PAPER+')','']
sk=next(r for r in ar if r['condition']=='Sidekick')
lines += [f'実際の標準Sidekickは、2.2 ms以内 **{sk["within_2.2_ms_pct"]:.2f}%**、5 ms以内 **{sk["within_5_ms_pct"]:.2f}%**、20 ms以内 **{sk["within_20_ms_pct"]:.2f}%** でした。約2%が20 msを超えるため、p99が約22 ms側へ乗ることは今回の分布から確認できます。小さな処理遅延だけではなく、フレーム周期に対応する尾が残っています。','',
'ただし、各22 ms標本がどのquACK/NACKイベントで生じたかは送信・フィードバック時刻の追加ログで確認が必要です。確定したのは分布上の尾の割合で、バッチ位相の寄与は時系列構造に基づく説明です。',
'以前の著者公開データの再集計では、標準Sidekickはp99=22.431 ms、p95=2.203 msでした。今回のp99=22.241 msは公開データ側に近いため、「本文2.2 msとの差＝今回の実装ミス」とは断定できません。本文と公開データの対応自体が未解明です。再集計は先頭5試行・floor(p×N)の記録に基づきます。',
'一方、E2Eのp99=64.083 msと公開データ52.436 msの差は残ります。NACK/再送の損失、連続欠損、試行数・実行時刻を、同条件の反復と個別イベント記録で切り分ける必要があります。','',
'### 3.3 図4B：CUBICのrollback差を追加実験で確認','',
'上流quiche 0.17.2は、小さな損失の後で輻輳窓を以前の値へ戻すヒューリスティックを持ちます。一方、著者公開forkではこの処理がコメントアウトされています。最初の実装はSidekick以外のこの調整を揃えておらず、E2E側が高い窓を保つ可能性がありました。',
'[上流CUBIC]('+UP+'quiche/src/recovery/cubic.rs) / [著者forkのCUBIC]('+FORK+'quiche/src/recovery/cubic.rs)','',
'別作業ツリーでこの処理だけを環境変数で切り替え、同じ検証バイナリで10 MB・近側1%損失を再測定しました。E2Eの順序はON→OFF→OFF→ONです。元の44試行は置き換えていません。','',
mdtable(['条件','rollback','n','各試行goodput (Mbit/s)','中央値','窓の復元回数（各試行）'],
[[('QUIC E2E' if r['mode']=='e2e' else '早期再送＋PACUBIC'),r['rollback'],r['trials'],', '.join(f(v) for v in r['trial_mbps']),
 f(r['median_mbps']),', '.join(map(str,r['restores']))] for r in abr]),'']
on=next(r for r in abr if r['mode']=='e2e' and r['rollback']=='on')
off=next(r for r in abr if r['mode']=='e2e' and r['rollback']=='off')
pa=next(r for r in abr if r['mode']=='pacubic')
lines += [f'E2EはONで中央値 **{on["median_mbps"]:.3f} Mbit/s**、OFFで **{off["median_mbps"]:.3f} Mbit/s** となりました。ONでは窓の復元が実際に発生し、OFFでは0回でした。この処理がE2E性能に大きく寄与したことを制御実験で確認できました。',
f'同じOFF条件で追加測定したPACUBICは中央値 **{pa["median_mbps"]:.3f} Mbit/s**、E2Eの約 **{pa["median_mbps"]/off["median_mbps"]:.2f}倍** でした。元の9.080 Mbit/sと新しいE2Eを混ぜて計算した比率ではありません。',
'当初の約1.12倍という小さな改善比の大きな原因は、E2EのCUBIC挙動を著者と揃えていなかった点にあると強く支持されます。ただし各条件2試行で損失列は同一ではなく、本文の3.6倍という値そのものの一致は未達です。',
'[B原因調査CSV](report_assets/cause_audit_b.csv)','',
'### 3.4 図4C：キューとCUBIC設定の追加検証','',
'設定損失0%でも、キューあふれやREDの早期dropは起こり得ます。元のHTB→netem（1,000 packets）と、公開設定に合わせたnetem→HTB→adaptive RED（4 BDP）を、rollback ON/OFFと組み合わせて検証しました。',
'全ケース10 MB・ACK間隔80 msです。RED limitは260,000 byte（52 ms×10 Mbit/sのBDP 65,000 byteの4倍）です。','',
mdtable(['キュー','rollback','輸送条件','n','各試行goodput (Mbit/s)','中央値','ACK数中央値','対象キューdrop中央値'],
[[r['queue'],r['rollback'],'E2E' if r['mode']=='e2e' else 'Sidekick ACK補助',r['trials'],', '.join(f(v) for v in r['trial_mbps']),
 f(r['median_mbps']),f'{r["median_ack_packets"]:g}',f'{r["median_queue_drops"]:g}'] for r in acr]),'',
'htbは元のHTB→netem、redはnetem→HTB→REDです。dropは4インターフェースの対象キューの合計を試行ごとに求め、その中央値です。REDではRED、htbではnetemの統計を用い、親子キューの同じdropを二重加算しません。QUICが損失と判定した数は再送や並べ替え判定を含み得るため、実キューdropとは分けています。','']
ct={(r['queue'],r['rollback'],r['mode']):r for r in acr}
h=ct[('htb','off','e2e')]['median_mbps'];red=ct[('red','off','e2e')]['median_mbps']
lines += [f'rollback OFFで比べると、E2Eは元のキューで **{h:.3f} Mbit/s**、REDで **{red:.3f} Mbit/s** でした。'+
 ('キューを合わせることで論文のE2E性能低下へ近づく方向の変化が確認できました。' if red<h else
  'この組合せでは、REDだけでE2Eが低下する仮説は支持されませんでした。'),
'ただし順序・容量・HTB設定・REDを公開プロファイルへまとめて変えており、それぞれの寄与は分離していません。論文の約4.5 Mbit/sを完全に説明・再現したとは扱いません。',
'**残る差：** 著者forkは最後のACK時刻も考慮してACK delayを計算し、今回は上流の最大受信パケットの受信時刻からの経過時間を使っています。ACKタイマー、RTT推定、pacing、MTU未満の送信制御、アプリ構成も同一ではありません。これらの寄与は未確定で、ACK delay・srtt・cwnd・送信時刻を並べた一要因検証が必要です。 [著者ACK生成]('+FORK+'quiche/src/lib.rs)','',
'**前の説明の訂正：** 64 MBの受信上限だけをCの主因とは言えません。著者公開nginx設定もinitial_max_data=1000m、initial_max_stream_data=100mという大きな値です。両方とも10 MBを上回る上限を与える経路があるため、「今回だけ受信窓が大きかったから」という説明は公開設定と整合しません。一方、代理によるフロー制御窓の前進が今回未実装である機能差自体は残ります。 [著者nginx設定]('+AUTHOR+'http3_integration/webserver/nginx.conf)','',
'**ACK数の差：** 著者描画は受信側NICのtx_packetsで、今回はACK-bearing QUIC packetsです。また、転送が速ければ同じ間隔でも送信期間が短くなります。今回のE2Eは80 Mbit÷9.014 Mbit/s÷0.08 s≒111回で、実測111個と対応します。個数差をACK生成ロジックだけの差とは判断しません。 [著者の計数]('+AUTHOR+'figures/fig4c_ack_reduction.py)',
'[C原因調査CSV](report_assets/cause_audit_c.csv)','',
'### 3.5 絶対goodputの単位差','',
'今回は10 MB=10,000,000 byteです。著者公開クライアントはGNU headへ10Mを渡す経路で、Mは2^20 byteなので、その経路のpayloadは10,485,760 byteになります。Ubuntu上でもhead -c 1Mが1,048,576 byteであることを確認しました。',
'描画側は10Mを10として10×8/timeを計算します。この公開経路なら実payload bit数から計算した値より約4.63%低く表示されます。絶対goodputの数%差を説明し得ますが、E2E/Sidekickの比に共通の係数なので3.6倍対1.12倍は説明できません。',
'[payload生成]('+AUTHOR+'mininet/client.py) / [goodput換算]('+AUTHOR+'figures/common.py)',
'論文の全測定がこの公開コミットと同じ経路だったとは保証せず、公開ログのsize_uploadとの照合が残っています。','']


lines += ['### 3.6 現時点の原因判定','',
mdtable(['論点','判定','根拠／残る検証'],
[['BのE2Eが高かった理由','追加実験で大きな寄与を確認','rollbackだけの切替で性能が大きく変わり、窓の復元回数も記録'],
['Aのp99が22 ms付近','分布上の理由は確認、個別イベント因果は未確定','約2%が20 ms超。バッチ待ちと整合するが時系列ログが必要'],
['Aの本文2.2 msと公開データ22.431 ms','未解明','独立実装だけの問題として断定しない'],
['Cのキュー差','追加検証で影響を評価','上表の測定に基づく。順序・容量・REDを個別には分離していない'],
['CのACK delay/タイマー/pacing/MTU制御','差は確認、寄与は未確定','一要因切替とsrtt/cwnd/送信イベント記録が必要'],
['64 MB受信上限が主因という説明','根拠不足のため撤回','著者公開設定にも大きな上限がある'],
['絶対goodputの数%差','公開スクリプト上の単位差を確認','本文の測定との対応はsize_upload照合が必要']]),'',
'## 4. 検証範囲・保存物・再実行','',
'### 4.1 検証件数と成功の意味','',
'- 最初のシナリオ検証：44試行成功（A=4、B=26、C=14）。通常条件のグラフはA=4、B=17、C=14の35試行。',
f'- 今回の原因調査：B={len(ab)}、C={len(ac)}、計{len(ab)+len(ac)}試行を別保存。元の44試行と混ぜて再集計していません。',
'- 基本実装はライブラリ462テスト、ランナー故障注入3テスト、新規Docker環境からの再ビルド・疎通を確認済みです。',
'- テスト成功はデータ保持・復号・会計等の検査に通った意味で、論文と同じ性能やTCP split-PEPとの公平性の保証ではありません。',
'- 反復数が少なく、E2E/Sidekickの損失列は同一ではありません。固定seedは試行順序用で、カーネルの損失乱数を固定しません。',
'- 最初の機能検証の初期部分では描画依存の導入も並行していました。厳密な評価では専用状態、反復増加、試行中の時間推移記録が必要です。','',
'### 4.2 保存物と来歴','',
'- 図・表の集計データ：[report_data.json](report_assets/report_data.json)',
'- 元の44試行の数値：[all_original_trials.csv](report_assets/all_original_trials.csv)',
'- 図・CSV・レポートのSHA-256：[report_manifest.json](report_assets/report_manifest.json)',
'- Ubuntuの原始データ：/home/gonsix/sidekick-independent/results/validation-final/',
'- 各試行にはmanifest.json、sender/receiver/proxy.log、Aのmedia.csv、pcap、tc-before/after.jsonがあります。pcapは96 byte snaplenです。',
'- 測定時ソース：results/validation-final/measured-source.tar.gz、差分：measured.patch、環境とソース/バイナリSHA-256：environment.json。',
'- B原因調査：results/cubic-cause-audit/、C原因調査：results/ack-cause-audit/。',
'- 追加検証用ソースは別ブランチsidekick-causal-audit、/home/gonsix/orca/workspaces/quiche/sidekick-causal-audit/です。',
'- 基本ソースは/home/gonsix/sidekick-independent/quiche/。最初の検証コミット5b54621、2条件CDF追加fdc6db7。測定時はソーススナップショットでも保存しています。',
'- Rust 1.100.0、Cargo.lock固定、Ubuntu 22.04専用コンテナ。kernel等はreport_data.jsonのmeasured_environmentを参照してください。','',
'### 4.3 再実行例','',
'以下はGonsix Ubuntuで実行します。出力先は毎回新しい名前にします。','',
'    docker exec sidekick-independent python3 reproduction/run.py \\',
'      --figure b --modes e2e,early,pacubic --trials 20 --size 10000000 \\',
'      --output /work/results/repeat-b-001','',
'    docker exec sidekick-independent python3 reproduction/run.py \\',
'      --figure c --modes e2e,ack --ack-ms 80 --trials 20 --size 10000000 \\',
'      --output /work/results/repeat-c-001','',
'この基本ランナーの例だけでは、原因調査用rollback切替やREDプロファイルを自動適用しません。最初の44試行と同じ系統の再測定です。',
'追加調査は別作業ツリーとrun_cause_audit.py / run_remaining_audits.pyで行いました。上流既定と、著者公開アーティファクトの調整を揃えた条件を区別して保存します。',
'論文規模へ反復数を増やす前に、ベースライン、キュー、ACK計数、実payload byte数を明示して固定する必要があります。','',
'### 4.4 主な参照資料','',
'- [Sidekick論文（NSDI 2024）]('+PAPER+')',
'- [今回照合した著者公開アーティファクト]('+AUTHOR.replace('/blob/','/tree/')+')',
'- [著者公開quiche fork]('+FORK.replace('/blob/','/tree/')+')',
'- [ベースのCloudflare quiche 0.17.2]('+UP.replace('/blob/','/tree/')+')','']
report='\n'.join(lines)+'\n'
report=report.replace('ACK数にはハンドシェイクのACKを含みます。','ACK数にはハンドシェイクのACKを含みます。設定上のランダム損失0%は、キューあふれ等のdropがないことを意味しません。')
report=report.replace('論文の約4.5 Mbit/sを完全に説明・再現したとは扱いません。','論文の約4.5 Mbit/sを完全に説明・再現したとは扱いません。drop数だけで性能の良否は決まらず、dropする位置・時刻、キュー待ち、輻輳窓の更新も効きます。')
(D/'RESULTS.md').write_text(report)
files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in A.iterdir() if p.is_file() and p.name!='report_manifest.json'}
save(A/'report_manifest.json',{'generated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'original_successful_trials':44,'primary_trials':35,'audit_b':len(ab),'audit_c':len(ac),
 'files':files,'results_md_sha256':hashlib.sha256(report.encode()).hexdigest()})
print('DETAILED_REPORT_READY',len(report),'characters')
print('B_AUDIT',abr)
print('C_AUDIT',acr)
