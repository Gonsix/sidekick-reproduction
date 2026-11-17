#!/usr/bin/env python3
"""Run isolated Figure 4 scenarios. Execute inside the dedicated privileged container."""
import argparse, dataclasses, hashlib, itertools, json, os, pathlib, platform, random
import secrets, signal, socket, subprocess, sys, time, uuid
ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / "target/release/examples"
MODES = {
    "e2e": (False, False, False, False),
    "observe": (True, False, False, False),
    "early": (True, True, False, False),
    "pacubic": (True, True, True, False),
    "ack": (True, False, False, True),
    "all": (True, True, True, True),
    "pacubic-only": (True, False, True, False),
    "early-ack": (True, True, False, True),
    "pacubic-ack": (True, False, True, True),
    "off-requested-all": (False, True, True, True),
}
def command(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, capture_output=True, **kwargs)
def save(path, data):
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)
def parse_log(path):
    rows = []
    for line in path.read_text().splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict) and "role" in value: rows.append(value)
        except json.JSONDecodeError: pass
    if len(rows) != 1: raise RuntimeError(f"expected one result in {path}, got {len(rows)}")
    return rows[0]
@dataclasses.dataclass
class Case:
    figure: str
    mode: str
    trial: int = 0
    size: int = 2_000_000
    seconds: int = 10
    ack_ms: int = 0
    multiplier: int = 1
    loss_override: float | None = None
    far_loss: float = 0.0
class Topology:
    def __init__(self, case, out):
        self.case, self.out = case, out
        self.prefix = "sk" + uuid.uuid4().hex[:8]
        self.ns = [self.prefix + x for x in ("s","r","d")]
        self.processes = []
        self.handles = []
        self.links = [self.prefix+"a",self.prefix+"b",self.prefix+"c",self.prefix+"e"]
    def nsrun(self, which, argv, **kwargs):
        return command(["ip","netns","exec",self.ns[which],*argv], **kwargs)
    def start(self, which, argv, name):
        log = (self.out / f"{name}.log").open("w")
        self.handles.append(log)
        p = subprocess.Popen(["ip","netns","exec",self.ns[which],*[str(x) for x in argv]],
                             stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        self.processes.append(p)
        return p
    def __enter__(self):
        try:
            for n in self.ns:
                command(["ip","netns","add",n])
                command(["ip","-n",n,"link","set","lo","up"])
            for left, right, ni, nj, li, rj in [
                (self.links[0],self.links[1],0,1,"s-eth","r-near"),
                (self.links[2],self.links[3],1,2,"r-far","d-eth")]:
                command(["ip","link","add",left,"type","veth","peer","name",right])
                command(["ip","link","set",left,"netns",self.ns[ni]])
                command(["ip","link","set",right,"netns",self.ns[nj]])
                command(["ip","-n",self.ns[ni],"link","set",left,"name",li])
                command(["ip","-n",self.ns[nj],"link","set",right,"name",rj])
            for ni, dev, addr in [(0,"s-eth","10.200.1.2/24"),(1,"r-near","10.200.1.1/24"),
                                  (1,"r-far","10.200.2.1/24"),(2,"d-eth","10.200.2.2/24")]:
                command(["ip","-n",self.ns[ni],"addr","add",addr,"dev",dev])
                command(["ip","-n",self.ns[ni],"link","set",dev,"up"])
                self.nsrun(ni,["ethtool","-K",dev,"tso","off","gso","off","gro","off"])
            self.nsrun(1,["sysctl","-qw","net.ipv4.ip_forward=1"])
            command(["ip","-n",self.ns[0],"route","add","default","via","10.200.1.1"])
            command(["ip","-n",self.ns[2],"route","add","default","via","10.200.2.1"])
            near=(25,10,0) if self.case.figure=="c" else (1,100,3.6 if self.case.figure=="a" else 1.0)
            if self.case.loss_override is not None: near=(near[0],near[1],self.case.loss_override)
            far=(1,100,self.case.far_loss) if self.case.figure=="c" else (25,10,self.case.far_loss)
            self.condition={"near":{"delay_ms":near[0],"mbps":near[1],"loss_pct":near[2]},
                            "far":{"delay_ms":far[0],"mbps":far[1],"loss_pct":far[2]},
                            "qdisc":"HTB + netem (limit 1000), each egress direction"}
            for ni, dev, config in [(0,"s-eth",near),(1,"r-near",near),(1,"r-far",far),(2,"d-eth",far)]:
                delay, rate, loss = config
                self.nsrun(ni,["tc","qdisc","add","dev",dev,"root","handle","1:","htb","default","1"])
                self.nsrun(ni,["tc","class","add","dev",dev,"parent","1:","classid","1:1","htb",
                               "rate",f"{rate}mbit","ceil",f"{rate}mbit","burst","15k"])
                self.nsrun(ni,["tc","qdisc","add","dev",dev,"parent","1:1","handle","10:","netem",
                               "delay",f"{delay}ms","loss","random",f"{loss}%","limit","1000"])
            return self
        except BaseException:
            self.__exit__(None,None,None)
            raise
    def snapshot(self, name):
        rows={}
        for ni,dev in [(0,"s-eth"),(1,"r-near"),(1,"r-far"),(2,"d-eth")]:
            rows[f"{self.ns[ni]}:{dev}"]=json.loads(self.nsrun(ni,["tc","-s","-j","qdisc","show","dev",dev]).stdout)
        save(self.out/f"tc-{name}.json",rows)
    def __exit__(self, typ, value, tb):
        for p in self.processes:
            if p.poll() is None: p.terminate()
        for p in self.processes:
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: p.kill();p.wait()
        for f in self.handles: f.close()
        for n in reversed(self.ns):
            subprocess.run(["ip","netns","del",n], capture_output=True)
        for link in self.links:
            subprocess.run(["ip","link","del",link], capture_output=True)
def flags(case):
    enabled,early,pacubic,ack=MODES[case.mode]
    return ["--sidekick","on" if enabled else "off","--early-retransmit","on" if early else "off",
            "--pacubic","on" if pacubic else "off","--ack-reduction","on" if ack else "off"]
def run_case(case,out,capture):
    out.mkdir(parents=True,exist_ok=False)
    manifest={"case":dataclasses.asdict(case),"status":"running","start_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
    save(out/"manifest.json",manifest)
    try:
        with Topology(case,out) as net:
            manifest["network"]=net.condition
            net.snapshot("before")
            ping=[]
            for addr in ["10.200.1.1","10.200.2.2"]:
                p=subprocess.run(["ip","netns","exec",net.ns[0],"ping","-c","4","-i","0.05","-W","1",addr],
                                 text=True,capture_output=True,timeout=8)
                ping.append({"address":addr,"status":p.returncode,"output":p.stdout+p.stderr})
            save(out/"calibration.json",ping)
            if capture:
                net.start(1,["tcpdump","-n","-i","r-near","-s","96","-U","-w",out/"near.pcap","udp"],"capture")
            token=secrets.token_hex(16)
            enabled=MODES[case.mode][0]
            threshold={"a":8,"b":10,"c":50}[case.figure]*case.multiplier
            every=2*case.multiplier if case.figure=="a" else 0
            ms=0 if case.figure=="a" else (30 if case.figure=="b" else 15)*case.multiplier
            timeout=max(30,case.seconds+10) if case.figure=="a" else max(45,int(case.size/200_000)+30)
            if enabled:
                proxy=net.start(1,[BIN/"sidekick_proxy","--iface","r-near","--bind","10.200.1.1:9000",
                    "--token",token,"--timeout",str(timeout+10)],"proxy")
            executable=BIN/("sidekick_media" if case.figure=="a" else "sidekick_transfer")
            common=["--bytes",str(case.size),"--timeout",str(timeout),"--ack-delay-ms",str(case.ack_ms)] if case.figure!="a" else [
                "--seconds",str(case.seconds),"--media-key",secrets.token_hex(16),"--timeout",str(timeout),
                "--csv",str(out/"media.csv")]
            receiver_cmd=[executable,"--role","receiver","--bind","10.200.2.2:4433",*common]
            receiver=net.start(2,receiver_cmd,"receiver")
            time.sleep(0.1)
            sender_cmd=[executable,"--role","sender","--bind","10.200.1.2:44330","--peer","10.200.2.2:4433",
                *common,*flags(case),"--threshold",str(threshold),"--quack-ms",str(ms),
                "--quack-packets",str(every),"--feedback","10.200.1.2:5103","--proxy","10.200.1.1:9000","--token",token]
            # Secret keys/tokens are deliberately omitted from persisted command lines.
            manifest["requested_features"]=dict(zip(["enabled","early_retransmit","pacubic","ack_reduction"],MODES[case.mode]))
            manifest["sidekick_parameters"]={"threshold":threshold,"interval_ms":ms,"every_packets":every}
            save(out/"manifest.json",manifest)
            sender=net.start(0,sender_cmd,"sender")
            try: status=sender.wait(timeout=timeout+5)
            except subprocess.TimeoutExpired: raise RuntimeError("sender watchdog expired")
            if status!=0: raise RuntimeError(f"sender failed ({status}), inspect {out/'sender.log'}")
            try: status=receiver.wait(timeout=8)
            except subprocess.TimeoutExpired: raise RuntimeError("receiver watchdog expired")
            if status!=0: raise RuntimeError(f"receiver failed ({status}), inspect {out/'receiver.log'}")
            send=parse_log(out/"sender.log");recv=parse_log(out/"receiver.log")
            if not send.get("verified") or not recv.get("verified"): raise RuntimeError("unverified transfer")
            if case.figure!="a":
                if send["bytes"]!=case.size or recv["bytes"]!=case.size or send["sha256"]!=recv["sha256"]:
                    raise RuntimeError("upload bytes/hash mismatch")
            elif recv["samples"]!=case.seconds*50: raise RuntimeError("incomplete media samples")
            if enabled and send["sidekick"]["valid"]==0: raise RuntimeError("no valid Robin feedback")
            if not enabled and send["sidekick"].get("valid",0)!=0: raise RuntimeError("master switch did not disable Sidekick")
            proxy_result=None
            if enabled:
                # Stop only this trial's observer, from the configured sender namespace.
                net.nsrun(0,["python3","-c",
                    "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                    f"s.sendto(b'SKX1'+bytes.fromhex('{token}'),('10.200.1.1',9000))"])
                if proxy.wait(timeout=3)!=0: raise RuntimeError("proxy failed")
                proxy_result=parse_log(out/"proxy.log")
            net.snapshot("after")
            result={"case":dataclasses.asdict(case),"sender":send,"receiver":recv,"proxy":proxy_result}
            save(out/"result.json",result)
            manifest["status"]="passed";save(out/"manifest.json",manifest)
            print(json.dumps({"status":"passed","case":dataclasses.asdict(case),
                "goodput_mbps":send.get("goodput_mbps"),"p99_ms":recv.get("p99_ms"),
                "ack_packets":recv.get("ack_packets"),"sidekick":send["sidekick"]}),flush=True)
            return result
    except BaseException as e:
        manifest["status"]="failed";manifest["error"]=str(e);save(out/"manifest.json",manifest)
        raise
def cases(a):
    figures=list("abc") if a.figure=="all" else [a.figure]
    rows=[]
    for f in figures:
        if a.suite=="paper":
            trials=a.trials or (5 if f=="a" else 20 if f=="b" else 5)
            for trial in range(trials):
                if f=="a":
                    rows.extend(Case(f,"e2e",trial,seconds=60) for _ in [0])
                    rows.extend(Case(f,"early",trial,seconds=60,multiplier=m) for m in [1,2,4])
                elif f=="b":
                    for size in [1,5,10,20,50]:
                        rows.append(Case(f,"e2e",trial,size=size*1_000_000))
                        rows.extend(Case(f,"pacubic",trial,size=size*1_000_000,multiplier=m) for m in [1,2,4])
                else:
                    for delay in [0,20,40,80,160,320]:
                        rows.append(Case(f,"e2e",trial,size=10_000_000,ack_ms=delay))
                        rows.extend(Case(f,"ack",trial,size=10_000_000,ack_ms=delay,multiplier=m) for m in [1,2,4])
        else:
            modes=a.modes.split(",") if a.modes else (["e2e","early"] if f=="a" else
                list(MODES) if a.suite=="ablation" else ["e2e","early","pacubic"] if f=="b" else ["e2e","ack"])
            for mode in modes:
                if mode not in MODES: raise ValueError(f"unknown mode {mode}")
                for trial in range(a.trials or 1):
                    for delay in ([0,80,320] if f=="c" and a.ack_ms is None else [a.ack_ms or 0]):
                        rows.append(Case(f,mode,trial,size=a.size,seconds=a.seconds,ack_ms=delay,
                                         loss_override=a.loss,far_loss=a.far_loss,multiplier=a.multiplier))
    random.Random(a.order_seed).shuffle(rows)
    return rows
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suite",choices=["smoke","ablation","paper"],default="smoke")
    p.add_argument("--figure",choices=["a","b","c","all"],default="all")
    p.add_argument("--output",type=pathlib.Path,required=True)
    p.add_argument("--trials",type=int)
    p.add_argument("--modes")
    p.add_argument("--seconds",type=int,default=10)
    p.add_argument("--size",type=int,default=2_000_000)
    p.add_argument("--ack-ms",type=int)
    p.add_argument("--loss",type=float)
    p.add_argument("--far-loss",type=float,default=0)
    p.add_argument("--multiplier",type=int,choices=[1,2,4],default=1)
    p.add_argument("--order-seed",type=int,default=20261117)
    p.add_argument("--no-capture",action="store_true")
    a=p.parse_args()
    if os.geteuid()!=0: p.error("run inside the dedicated privileged container")
    if a.output.exists(): p.error("output must be new; existing results are never reused")
    if a.size<1 or a.seconds<1 or a.size>50_000_000: p.error("invalid size/duration")
    rows=cases(a);a.output=a.output.resolve();a.output.mkdir(parents=True)
    provenance={"argv":sys.argv,"platform":platform.platform(),"cpu":platform.processor(),
        "base_commit":command(["git","rev-parse","HEAD"],cwd=ROOT).stdout.strip(),
        "branch":command(["git","branch","--show-current"],cwd=ROOT).stdout.strip(),
        "rustc":command(["rustc","-Vv"],cwd=ROOT).stdout,
        "cargo_lock_sha256":hashlib.sha256((ROOT/"Cargo.lock").read_bytes()).hexdigest(),
        "tracked_diff_sha256":hashlib.sha256(command(["git","diff"],cwd=ROOT).stdout.encode()).hexdigest(),
        "tc":command(["tc","-V"]).stdout,"cases":[dataclasses.asdict(c) for c in rows],
        "kernel_loss_seed":"netem uses kernel randomness; order_seed only shuffles trial order"}
    provenance["source_sha256"] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for base in [ROOT/"quiche/src",ROOT/"quiche/examples",ROOT/"reproduction"]
        for p in base.rglob("*") if p.is_file() and p.suffix in [".rs",".py",".md"]}
    provenance["binary_sha256"] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in BIN.glob("sidekick_*") if p.is_file() and p.suffix==""}
    save(a.output/"environment.json",provenance)
    def interrupted(signum,frame):
        raise KeyboardInterrupt(f"received signal {signum}")
    signal.signal(signal.SIGTERM,interrupted)
    results=[]
    for i,c in enumerate(rows):
        name=f"{i:03d}-{c.figure}-{c.mode}-n{c.size}-ack{c.ack_ms}-x{c.multiplier}-trial{c.trial}"
        results.append(run_case(c,a.output/name,not a.no_capture))
        save(a.output/"results.json",results)
    save(a.output/"completed.json",{"trials":len(results),"status":"passed"})
if __name__=="__main__": main()
