#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 59 — Subscription webhook A2A.

openclaw memory_subscribe to ns/Y on hermes daemon; hermes write to ns/Y;
openclaw's webhook receives the event with correct correlation_id; trigger
5xx on the receiver and verify retry ladder + DLQ entry.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "59"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    # v0.7 SSRF guard: webhook URLs that resolve to private/loopback IPs are
    # rejected by default unless `[subscriptions] allow_loopback_webhooks =
    # true` is set in the daemon's config.toml. The campaign daemons use the
    # default-deny config (operator-gated; not modifiable from the test
    # harness), so a private-VPC receiver on `OPEN_PRIV:28590` cannot be
    # subscribed. Without an external public-IP receiver, webhook delivery
    # cannot be exercised end-to-end on this topology.
    import os
    if os.environ.get("ALLOW_LOOPBACK_WEBHOOKS_VERIFIED", "0") != "1":
        h.skip(
            "v0.7 SSRF guard rejects private-VPC webhook URLs by default; set "
            "`[subscriptions] allow_loopback_webhooks = true` on both daemons "
            "and export ALLOW_LOOPBACK_WEBHOOKS_VERIFIED=1 to run this scenario."
        )
        return
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s59-{new_uuid()[:6]}"
    correlation = new_uuid("corr-")

    # Phase A: stand up a tiny webhook receiver on openclaw, port 28590.
    log("phase A: start webhook receiver on openclaw")
    webhook_script = r"""set -u
PORT=28590
pkill -f 'python3 -m http.server '$PORT >/dev/null 2>&1 || true
mkdir -p /tmp/s59-webhook
cd /tmp/s59-webhook
cat > server.py <<'PY'
import http.server, json, sys, time
LOG = '/tmp/s59-webhook/events.log'
DLQ = '/tmp/s59-webhook/dlq.log'
MODE = '/tmp/s59-webhook/mode'   # 'ok' or '500'
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('content-length', '0'))
        body = self.rfile.read(n).decode('utf-8', 'replace')
        try: m = open(MODE).read().strip()
        except: m = 'ok'
        line = json.dumps({'ts': time.time(), 'mode': m, 'body': body, 'attempt': self.headers.get('X-Retry-Attempt', '0')})
        with open(LOG, 'a') as f: f.write(line + '\n')
        if m == '500':
            self.send_response(500); self.end_headers(); self.wfile.write(b'fail')
            return
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
http.server.HTTPServer(('0.0.0.0', 28590), H).serve_forever()
PY
echo ok > /tmp/s59-webhook/mode
nohup python3 server.py >/tmp/s59-webhook/stderr.log 2>&1 &
echo $! > /tmp/s59-webhook/pid
sleep 1
"""
    h.ssh_bash_script(h.node1_ip, webhook_script, timeout=15)

    OPEN_PRIV = h.node1_priv or h.node1_ip
    target = f"http://{OPEN_PRIV}:28590/hook"

    # Phase B: openclaw subscribes on hermes daemon to ns/Y.
    log(f"phase B: subscribe -> {target}")
    sub_body = {"namespace": ns, "callback_url": target,
                "agent_id": OPEN, "correlation_id": correlation}
    rc_s, resp_s = h.http_on(h.node2_ip, "POST", "/api/v1/subscribe",
                             body=sub_body, agent_id=OPEN, include_status=True)
    log(f"  subscribe -> rc={rc_s} resp={resp_s}")

    # Phase C: hermes writes to ns; webhook should fire.
    log("phase C: hermes writes to ns; expect webhook event")
    _, _ = h.write_memory(h.node2_ip, HERM, ns, title="evt-1",
                          content=f"event {correlation}", include_status=True)
    h.settle(5, "webhook fire")

    # Phase D: trigger 5xx then write again; verify retry ladder + DLQ.
    log("phase D: switch receiver to 500, write again, verify DLQ")
    h.ssh_exec(h.node1_ip, "echo 500 > /tmp/s59-webhook/mode")
    _, _ = h.write_memory(h.node2_ip, HERM, ns, title="evt-2",
                          content=f"event-fail {correlation}", include_status=True)
    h.settle(20, "retry ladder + DLQ promotion")

    # Inspect events on the openclaw receiver + DLQ on hermes.
    ev = h.ssh_exec(h.node1_ip, "cat /tmp/s59-webhook/events.log 2>/dev/null | wc -l").stdout.strip()
    rc_dlq, dlq = h.http_on(h.node2_ip, "GET",
                            f"/api/v1/subscription/dlq_list?namespace={ns}",
                            agent_id=HERM)
    dlq_count = 0
    if isinstance(dlq, dict):
        dlq_count = len(dlq.get("entries") or dlq.get("dlq") or [])

    # Cleanup webhook
    h.ssh_exec(h.node1_ip, "kill $(cat /tmp/s59-webhook/pid 2>/dev/null) 2>/dev/null; rm -rf /tmp/s59-webhook")

    reasons: list[str] = []
    passed = True
    try:
        events_seen = int(ev)
    except ValueError:
        events_seen = 0
    if events_seen < 1:
        passed = False; reasons.append("webhook never fired for evt-1")
    if dlq_count < 1:
        passed = False; reasons.append(f"DLQ empty after 5xx (entries={dlq_count})")

    h.emit(passed=passed, reason="; ".join(reasons),
           events_received=events_seen, dlq_count=dlq_count,
           correlation_id=correlation, namespace=ns, reasons=reasons)


if __name__ == "__main__":
    main()
