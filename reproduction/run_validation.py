import argparse
import pathlib
import random
from run import Case, run_case, save, command, ROOT, BIN
import hashlib, platform, dataclasses
parser = argparse.ArgumentParser(description="Run the 44 validation scenarios")
parser.add_argument("--output", type=pathlib.Path, required=True)
out = parser.parse_args().output.resolve()
out.mkdir(parents=True,exist_ok=False)
cases=[]
# Exercise all eight feature combinations, observation-only, and master-off override.
from run import MODES
for mode in MODES: cases.append(Case("b",mode,size=2_000_000))
for trial in range(2):
    for size in [1_000_000,10_000_000]:
        for mode in ["e2e","early","pacubic"]:cases.append(Case("b",mode,trial,size=size))
for m in [2,4]:cases.append(Case("b","pacubic",size=10_000_000,multiplier=m))
for trial in range(2):
    for ack in [0,80,320]:
        for mode in ["e2e","ack"]:cases.append(Case("c",mode,trial,size=10_000_000,ack_ms=ack))
for m in [2,4]:cases.append(Case("c","ack",size=10_000_000,ack_ms=320,multiplier=m))
# Base fallbacks with far-path loss, beyond the proxy.
for mode in ["e2e","all"]:cases.append(Case("b",mode,size=2_000_000,loss_override=0,far_loss=1))
random.Random(20261117).shuffle(cases)
cases += [Case("a","e2e",seconds=60)] + [Case("a","early",seconds=60,multiplier=m) for m in [1,2,4]]
save(out/"environment.json",{"base":command(["git","rev-parse","HEAD"],cwd=ROOT).stdout.strip(),
    "branch":command(["git","branch","--show-current"],cwd=ROOT).stdout.strip(),
    "platform":platform.platform(),"rustc":command(["rustc","-Vv"],cwd=ROOT).stdout,
    "cargo_lock_sha256":hashlib.sha256((ROOT/"Cargo.lock").read_bytes()).hexdigest(),
    "source_sha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for base in [ROOT/"quiche/src",ROOT/"quiche/examples"] for p in base.rglob("*.rs") if p.is_file()},
    "binary_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in BIN.glob("sidekick_*")
        if p.is_file() and p.suffix==""},
    "cases":[dataclasses.asdict(c) for c in cases],"kernel_loss_seed":"uncontrolled kernel RNG",
    "note":"short-run validation and representative one-minute media; not full paper trial count"})
results=[]
for i,c in enumerate(cases):
    name=f"{i:03d}-{c.figure}-{c.mode}-n{c.size}-ack{c.ack_ms}-x{c.multiplier}-trial{c.trial}"
    results.append(run_case(c,out/name,True))
    save(out/"results.json",results)
save(out/"completed.json",{"status":"passed","trials":len(results)})
print("VALIDATION_COMPLETE",len(results),flush=True)
