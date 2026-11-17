# CUBIC / queue cause audit

Base: fdc6db75a942bb1554adf0ad07f271aefd4523f3.
The experimental gate SIDEKICK_AUDIT_DISABLE_ROLLBACK disables only the small-loss CUBIC heuristic; it does not disable all spurious-loss rollback paths.

The same instrumented release binary is used for ON/OFF.
Successful heuristic restorations are counted in audit_heuristic_rollbacks.
B: near loss 1%, 10,000,000-byte POST, E2E ON/OFF and PACUBIC OFF.
C: 10,000,000-byte POST, ACK interval 80 ms, configured random loss 0%, HTB-netem or netem-HTB-adaptive RED, with rollback ON/OFF.
RED limit is 260,000 bytes (4 BDP), avpkt 1000, adaptive harddrop.
See run_cause_audit.py and run_remaining_audits.py.

Observed datasets:
- /home/gonsix/sidekick-independent/results/cubic-cause-audit/
- /home/gonsix/sidekick-independent/results/ack-cause-audit/

The runtime build snapshot is /home/gonsix/sidekick-independent/causal-audit-src in the dedicated container mount.
These results are additional controlled trials and do not replace the original 44 trials.
Loss traces are independent kernel RNG samples; there are two trials per setting.
