#!/usr/bin/env python3
"""Aggregate successful independent measurements; never substitute author data."""
import argparse, csv, collections, json, math, pathlib, statistics
def quantile(values,p):
    values=sorted(values)
    return values[min(len(values)-1,int(math.floor(p*len(values))))]
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs",nargs="+",type=pathlib.Path)
    ap.add_argument("--output",required=True,type=pathlib.Path)
    ap.add_argument("--standard-only",action="store_true")
    ap.add_argument("--no-plots",action="store_true")
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    results=[];failed=[];seen=set();seen_manifests=set()
    for folder in a.inputs:
        for p in sorted(folder.rglob("manifest.json")):
            if p.resolve() in seen_manifests:continue
            seen_manifests.add(p.resolve())
            m=json.loads(p.read_text())
            if m["status"]!="passed":failed.append({"path":str(p),"status":m["status"],"error":m.get("error")})
        for p in sorted(folder.rglob("result.json")):
            if p.resolve() in seen:continue
            manifest=p.parent/"manifest.json"
            if not manifest.exists() or json.loads(manifest.read_text()).get("status")!="passed":continue
            seen.add(p.resolve())
            d=json.loads(p.read_text());d["_path"]=str(p.parent);results.append(d)
    if a.standard_only:
        results=[d for d in results if d["case"]["loss_override"] is None and d["case"]["far_loss"]==0 and
            d["case"]["mode"] in {"a":["e2e","early"],"b":["e2e","early","pacubic"],"c":["e2e","ack"]}[d["case"]["figure"]]]
    if not results:raise SystemExit("no successful measurements")
    def label(c):
        base={"e2e":"QUIC E2E","early":"Early retransmission","pacubic":"PACUBIC + early",
              "ack":"ACK reduction","all":"All features","observe":"Observe only",
              "off-requested-all":"Master off","pacubic-only":"PACUBIC only"}.get(c["mode"],c["mode"])
        if c["figure"]=="a":base="Simple E2E" if c["mode"]=="e2e" else "Sidekick"
        if c["far_loss"]:base+=f' [far loss {c["far_loss"]}%]'
        if c["loss_override"] is not None:base+=f' [near loss {c["loss_override"]}%]'
        return base+(f' ({c["multiplier"]}x)' if c["multiplier"]!=1 else "")
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,
                         "axes.grid":True,"grid.alpha":0.18,"figure.dpi":150})
    groups=collections.defaultdict(list)
    for d in results:
        c=d["case"];groups[(c["figure"],label(c))].append(d)
    summaries=[];table=[]
    for (fig,name),data in groups.items():
        if fig=="a":
            samples=[]
            for d in data:
                with (pathlib.Path(d["_path"])/"media.csv").open() as f:
                    samples.extend(int(r["jitter_ns"])/1e6 for r in csv.DictReader(f))
            summaries.append({"figure":fig,"label":name,"trials":len(data),"samples":len(samples),
                "p95_ms":quantile(samples,.95),"p99_ms":quantile(samples,.99),"p999_ms":quantile(samples,.999)})
        for d in data:
            c=d["case"];s=d["sender"];r=d["receiver"]
            table.append({"figure":fig,"label":name,"trial":c["trial"],"size":c["size"],
                "ack_ms":c["ack_ms"],"multiplier":c["multiplier"],"seconds":s.get("seconds"),
                "goodput_mbps":s.get("goodput_mbps"),"ack_packets":r.get("ack_packets"),
                "p99_ms":r.get("p99_ms"),"quack_retransmissions":s.get("retrans_quack",s.get("early_retransmissions")),
                "valid_quacks":s["sidekick"].get("valid"),"rejected_quacks":s["sidekick"].get("rejected"),
                "resets":s["sidekick"].get("resets"),"result_directory":d["_path"]})
    for fig in ([] if a.no_plots else ["a","b","c"]):
        sub={k[1]:v for k,v in groups.items() if k[0]==fig}
        if not sub:continue
        figobj,ax=plt.subplots(figsize=(6.8,4.5))
        for name,data in sub.items():
            if fig=="a":
                samples=[]
                for d in data:
                    with (pathlib.Path(d["_path"])/"media.csv").open() as f:
                        samples.extend(int(r["jitter_ns"])/1e6 for r in csv.DictReader(f))
                ps=[.86+i*.001 for i in range(140)]
                ax.plot([quantile(samples,p) for p in ps],[p*100 for p in ps],label=name)
                ax.set(xlabel="De-jitter buffer delay (ms)",ylabel="Percentile (%)",ylim=(86,100))
            elif fig=="b":
                sizes=collections.defaultdict(list)
                for d in data:sizes[d["case"]["size"]].append(d["sender"]["goodput_mbps"])
                xs=sorted(sizes);med=[statistics.median(sizes[x]) for x in xs]
                lo=[m-quantile(sizes[x],.25) for x,m in zip(xs,med)]
                hi=[quantile(sizes[x],.75)-m for x,m in zip(xs,med)]
                ax.errorbar([x/1e6 for x in xs],med,yerr=[lo,hi],marker="o",capsize=3,label=name)
                ax.set(xlabel="HTTP/3 upload size (MB, decimal)",ylabel="Goodput (Mbit/s)")
            else:
                ax.scatter([d["receiver"]["ack_packets"] for d in data],
                           [d["sender"]["goodput_mbps"] for d in data],label=name,alpha=.8,s=30)
                ax.set(xlabel="ACK-bearing QUIC packets from receiver",ylabel="Goodput (Mbit/s)")
        ax.legend(fontsize=8);ax.set_title(f"Independent Sidekick reproduction — Figure 4({fig})")
        figobj.tight_layout()
        for ext in ["png","svg"]:figobj.savefig(a.output/f"fig4{fig}.{ext}")
        plt.close(figobj)
    with (a.output/"trials.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)
    (a.output/"summary.json").write_text(json.dumps({"successful_trials":len(results),"media":summaries,
        "failed_trials":failed,"inputs":[str(p) for p in a.inputs]},indent=2)+"\n")
    lines=["# 実測結果（独立実装）","",f"成功試行数: {len(results)}。失敗試行数: {len(failed)}。",
        "公開データは混入していません。各試行の生ログ・環境情報は result_directory を参照してください。",
        "","|図|条件|試行数|測定値|","|---|---|---:|---|"]
    for (fig,name),data in sorted(groups.items()):
        if fig=="a":
            row=next(x for x in summaries if x["figure"]==fig and x["label"]==name)
            value=f'p95={row["p95_ms"]:.3f}, p99={row["p99_ms"]:.3f}, p99.9={row["p999_ms"]:.3f} ms; N={row["samples"]}'
            lines.append(f"|4a|{name}|{len(data)}|{value}|")
        else:
            by=collections.defaultdict(list)
            for d in data:by[(d["case"]["size"],d["case"]["ack_ms"])].append(d)
            for (size,ack),ds in sorted(by.items()):
                med=statistics.median(d["sender"]["goodput_mbps"] for d in ds)
                ack_n=statistics.median(d["receiver"]["ack_packets"] for d in ds)
                lines.append(f"|4{fig}|{name}; {size/1e6:g} MB; ACK {ack} ms|{len(ds)}|median {med:.3f} Mbit/s; receiver ACK {ack_n:g}|")
    lines+=["","数値はこの環境・実装・試行数の測定値です。論文本文の数値一致や輻輳制御の公平性を保証するものではありません。",
        "4aの分位点は全成功試行の全標本（0も含む）を連結し、floor(p*N) 番目を使います。",
        "4bの誤差棒は試行間の第1・第3四分位。4cの点は各試行の実測ACK数です。"]
    (a.output/"REPORT.md").write_text("\n".join(lines)+"\n")
    print(json.dumps({"trials":len(results),"media":summaries,"output":str(a.output)}))
if __name__=="__main__":main()
