from pathlib import Path
import os,sys,json,hashlib,subprocess
sys.path.insert(0,"/work/causal-audit-src/reproduction")
from run import Case, run_case,save
out=Path('/work/results/cubic-cause-audit')
out.mkdir(parents=True,exist_ok=False)
source=Path('/work/causal-audit-src')
save(out/'environment.json',{
 "base_commit":"fdc6db75a942bb1554adf0ad07f271aefd4523f3",
 "audit_source":"/home/gonsix/orca/workspaces/quiche/sidekick-causal-audit",
 "source_sha256":{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
   for p in (source/'quiche/src').rglob('*.rs')},
 "binary_sha256":hashlib.sha256((source/'target/release/examples/sidekick_transfer').read_bytes()).hexdigest(),
 "difference":"Only CUBIC small-loss heuristic rollback is toggled at process start; Sidekick OFF in both groups.",
 "order":["on","off","off","on"],
 "kernel_loss_seed":"Uncontrolled kernel RNG, independent trials; not identical loss traces."
})
results=[]
for i,mode in enumerate(["on","off","off","on"]):
 if mode=="off":os.environ["SIDEKICK_AUDIT_DISABLE_ROLLBACK"]="1"
 else:os.environ.pop("SIDEKICK_AUDIT_DISABLE_ROLLBACK",None)
 case=Case("b","e2e",trial=i,size=10_000_000)
 result=run_case(case,out/f'{i:02d}-rollback-{mode}',False)
 result["audit_rollback_setting"]=mode
 save(out/f'{i:02d}-rollback-{mode}'/'result.json',result)
 results.append(result);save(out/'results.json',results)
 print("AUDIT_RESULT",mode,round(result['sender']['goodput_mbps'],3),
       "rollbacks",result['sender']['audit_heuristic_rollbacks'],flush=True)
save(out/'completed.json',{"trials":len(results),"status":"passed"})
print("CUBIC_AUDIT_COMPLETE",flush=True)
