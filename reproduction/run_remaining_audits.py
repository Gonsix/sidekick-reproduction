from pathlib import Path
import os,sys,json
import run
from run import Case,run_case,save
import argparse
parser = argparse.ArgumentParser(description="Complete the Figure 4B audit and run the Figure 4C queue audit")
parser.add_argument("--output-root", type=Path, default=Path("results"))
args = parser.parse_args()
output_root = args.output_root.resolve()
out = output_root / "cubic-cause-audit"
results=json.loads((out/'results.json').read_text())
os.environ["SIDEKICK_AUDIT_DISABLE_ROLLBACK"]="1"
for i in range(2):
 result=run_case(Case("b","pacubic",trial=i,size=10_000_000),out/f'04-pacubic-rollback-off-{i}',False)
 result["audit_rollback_setting"]="off"
 save(out/f'04-pacubic-rollback-off-{i}'/'result.json',result)
 results.append(result);save(out/'results.json',results)
 print("AUDIT_PACUBIC_OFF",round(result["sender"]["goodput_mbps"],4),flush=True)
save(out/'completed.json',{"trials":len(results),"status":"passed"})
OriginalTopology=run.Topology
class RedTopology(OriginalTopology):
 def __enter__(self):
  super().__enter__()
  try:
   near=self.condition["near"];far=self.condition["far"]
   limit=int((near["delay_ms"]+far["delay_ms"])*2*min(near["mbps"],far["mbps"])*1_000_000/1000/8*4)
   for ni,dev,c in [(0,"s-eth",near),(1,"r-near",near),(1,"r-far",far),(2,"d-eth",far)]:
    self.nsrun(ni,["tc","qdisc","replace","dev",dev,"root","handle","2:","netem",
     "loss",f'{c["loss_pct"]}%',"delay",f'{c["delay_ms"]}ms'])
    self.nsrun(ni,["tc","qdisc","add","dev",dev,"parent","2:","handle","3:","htb","default","10"])
    self.nsrun(ni,["tc","class","add","dev",dev,"parent","3:","classid","3:10","htb","rate",f'{c["mbps"]}mbit'])
    self.nsrun(ni,["tc","qdisc","add","dev",dev,"parent","3:10","handle","11:","red",
     "limit",str(limit),"avpkt","1000","adaptive","harddrop","bandwidth",f'{c["mbps"]}mbit'])
   self.condition["qdisc"]="netem -> HTB -> adaptive RED harddrop"
   self.condition["red_limit_bytes"]=limit
   return self
  except BaseException:
   self.__exit__(None,None,None);raise
out = output_root / "ack-cause-audit"
out.mkdir(exist_ok=False)
save(out/'environment.json',{"binary":"same audit binary as cubic-cause-audit",
 "controlled_factors":["CUBIC rollback on/off","HTB-netem / netem-HTB-RED"],
 "case":"Figure 4C, 10 MB, ACK interval 80 ms, 0% configured random loss",
 "note":"RED may add congestion drops. Kernel loss traces are not paired."})
results=[];failures=[]
plan=[("htb","on","e2e"),("red","off","e2e"),("red","on","e2e"),("htb","off","e2e"),
      ("red","off","ack"),("htb","off","ack"),
      ("htb","off","e2e"),("red","on","e2e"),("red","off","e2e"),("htb","on","e2e"),
      ("htb","off","ack"),("red","off","ack")]
for i,(queue,rollback,mode) in enumerate(plan):
 run.Topology=RedTopology if queue=="red" else OriginalTopology
 if rollback=="off":os.environ["SIDEKICK_AUDIT_DISABLE_ROLLBACK"]="1"
 else:os.environ.pop("SIDEKICK_AUDIT_DISABLE_ROLLBACK",None)
 folder=out/f'{i:02d}-{queue}-rollback-{rollback}-{mode}'
 try:
  result=run_case(Case("c",mode,trial=i,size=10_000_000,ack_ms=80),folder,False)
  result["audit_queue"]=queue;result["audit_rollback_setting"]=rollback
  save(folder/'result.json',result);results.append(result);save(out/'results.json',results)
  print("ACK_CAUSE",queue,rollback,mode,round(result['sender']['goodput_mbps'],4),
    "lost",result['sender']['lost_packets'],"rollbacks",result['sender']['audit_heuristic_rollbacks'],flush=True)
 except Exception as e:
  failures.append({"queue":queue,"rollback":rollback,"mode":mode,"error":str(e)})
  save(out/'failures.json',failures);print("ACK_AUDIT_FAILED",str(e),flush=True)
save(out/'completed.json',{"successful_trials":len(results),"failed_trials":len(failures),
 "status":"passed" if not failures else "completed-with-failures"})
print("REMAINING_AUDITS_COMPLETE",flush=True)
