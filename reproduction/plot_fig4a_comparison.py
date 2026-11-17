#!/usr/bin/env python3
"""Paper-style empirical CDF for the independent Figure 4A measurements."""
import argparse
import bisect
import collections
import csv
import hashlib
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    rows={"e2e":[],"early":[]}
    sources={"e2e":[],"early":[]}
    for path in sorted(args.results.rglob("result.json")):
        manifest=path.parent/"manifest.json"
        if not manifest.exists() or json.loads(manifest.read_text()).get("status")!="passed":
            continue
        result=json.loads(path.read_text())
        case=result["case"]
        if (case["figure"]!="a" or case["mode"] not in rows or case["multiplier"]!=1
            or case.get("loss_override") is not None or case.get("far_loss",0)!=0):
            continue
        source=path.parent/"media.csv"
        with source.open() as handle:
            samples=[int(row["jitter_ns"])/1e6 for row in csv.DictReader(handle)]
        if len(samples)!=result["receiver"]["samples"] or any(x<0 for x in samples):
            raise ValueError(f"invalid samples: {source}")
        rows[case["mode"]].extend(samples)
        sources[case["mode"]].append({"csv":str(source),"sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
            "seconds":case["seconds"],"samples":len(samples)})
    for mode,values in rows.items():
        if not values:raise ValueError(f"no successful standard Figure 4A data for {mode}")
        values.sort()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":11,"svg.fonttype":"none",
        "axes.spines.top":False,"axes.spines.right":False})
    cutoffs=[50,100,150,200]
    labels={"e2e":"Simple E2E","early":"Sidekick"}
    colors={"e2e":"#C98124","early":"#2177A5"}
    summary={}
    for mode,values in rows.items():
        counts={str(t):bisect.bisect_right(values,t) for t in cutoffs}
        summary[labels[mode]]={"samples":len(values),"trials":len(sources[mode]),
            "within_counts":counts,"within_percent":{t:100*n/len(values) for t,n in counts.items()},
            "p99_ms":values[min(len(values)-1,int(.99*len(values)))],
            "max_ms":values[-1],"sources":sources[mode]}
    fig=plt.figure(figsize=(7.6,6.2),facecolor="white")
    grid=fig.add_gridspec(2,1,height_ratios=[4.5,1.05],hspace=.34)
    ax=fig.add_subplot(grid[0])
    for mode in ["e2e","early"]:
        values=rows[mode]
        unique=sorted({x for x in values if x<=200})
        xs=[0]+unique+[200]
        ys=[0]+[100*bisect.bisect_right(values,x)/len(values) for x in unique]+[
            100*bisect.bisect_right(values,200)/len(values)]
        ax.step(xs,ys,where="post",color=colors[mode],linewidth=2,
            linestyle="--" if mode=="e2e" else "-",label=labels[mode])
        ax.scatter(cutoffs,[summary[labels[mode]]["within_percent"][str(t)] for t in cutoffs],
            color=colors[mode],s=24,marker="o" if mode=="e2e" else "s",zorder=3)
    ax.set(xlim=(0,200),ylim=(86,100.25),xticks=[0,50,100,150,200],
        yticks=list(range(86,101,2)),xlabel="De-jitter latency (ms)",ylabel="Cumulative packets (%)")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
    ax.grid(color="#D9DEE3",linewidth=.65,alpha=.8)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right",frameon=False,fontsize=11)
    ax.set_title("Figure 4A: Simple E2E vs. Sidekick",fontsize=14,pad=14)
    table_ax=fig.add_subplot(grid[1]);table_ax.axis("off")
    cells=[]
    for mode in ["e2e","early"]:
        cells.append([labels[mode]]+[f'{summary[labels[mode]]["within_percent"][str(t)]:.2f}%' for t in cutoffs])
    table=table_ax.table(cellText=cells,colLabels=["Delay threshold"]+[f"≤ {t} ms" for t in cutoffs],
        cellLoc="center",loc="center",colWidths=[.26,.185,.185,.185,.185])
    table.auto_set_font_size(False);table.set_fontsize(10);table.scale(1,1.48)
    for (row,col),cell in table.get_celld().items():
        cell.set_edgecolor("#D9DEE3");cell.set_linewidth(.65)
        if row==0:cell.set_facecolor("#EEF2F5");cell.set_text_props(weight="bold")
        elif col==0:cell.set_text_props(color=colors["e2e" if row==1 else "early"],weight="bold")
    fig.subplots_adjust(left=.13,right=.97,top=.9,bottom=.11)
    n_text=" / ".join(f"{labels[m]}: n={len(rows[m]):,}" for m in ["e2e","early"])
    fig.text(.13,.048,n_text+"; 60 s per condition.",fontsize=9,color="#46515B")
    fig.text(.13,.020,"All packets included. CDF viewport: 86–100%. Independent implementation measurements.",
        fontsize=8.5,color="#46515B")
    args.output.mkdir(parents=True,exist_ok=True)
    stem=args.output/"fig4a_e2e_sidekick_cdf"
    fig.savefig(str(stem)+".png",dpi=200)
    fig.savefig(str(stem)+".svg")
    plt.close(fig)
    (args.output/"fig4a_cdf_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    with (args.output/"fig4a_cdf_thresholds.csv").open("w",newline="") as handle:
        writer=csv.writer(handle);writer.writerow(["condition","delay_threshold_ms","within_packets","total_packets","within_percent"])
        for name,data in summary.items():
            for t in cutoffs:writer.writerow([name,t,data["within_counts"][str(t)],data["samples"],data["within_percent"][str(t)]])
    lines=["# 図4A: Simple E2E と Sidekick の累積分布","",
        "各遅延以内に収まった全パケットの割合（累積割合）です。縦軸86〜100%は論文と同じ拡大表示です。",
        "","![図4Aの比較](fig4a_e2e_sidekick_cdf.png)","",
        "|条件|50 ms以内|100 ms以内|150 ms以内|200 ms以内|","|---|---:|---:|---:|---:|"]
    for name,data in summary.items():
        lines.append("|"+name+"|"+"|".join(f'{data["within_percent"][str(t)]:.2f}%' for t in cutoffs)+"|")
    lines+=["","各条件60秒・3,000標本・1試行。0 msも含む全標本を分母にしています。",
        "Sidekickは2パケットごと・t=8の標準設定です。新しい通信試行は行わず、保存済みの独立実装の実測CSVから作図しました。",
        "論文の公開生データは混入していません。"]
    (args.output/"FIG4A_COMPARISON.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({name:{"n":d["samples"],"within_percent":d["within_percent"]} for name,d in summary.items()}))
if __name__=="__main__":main()
