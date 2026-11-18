from pathlib import Path
import os,sys,json,hashlib,subprocess
from run import Case, run_case,save
import argparse
parser = argparse.ArgumentParser(description="Run the Figure 4B rollback audit")
parser.add_argument("--output-root", type=Path, default=Path("results"))
args = parser.parse_args()
out = args.output_root.resolve() / "cubic-cause-audit"
out.mkdir(parents=True,exist_ok=False)
source=Path(__file__).resolve().parents[1]
save(out/'environment.json',{
 "commit":subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
 "audit_source":str(source),
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
