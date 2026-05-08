#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 54 — identity --force flag.

S54 identity --force flag: ephemeral key dir; first generate succeeds; second
without --force errors with "pass --force"; third with --force rotates;
legacy --no-overwrite is a hidden no-op.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "54"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    node = h.node1_ip  # any node — single-node check
    keydir = f"/tmp/s54-keys-{new_uuid()[:8]}"

    script = f"""set -u
KD="{keydir}"
mkdir -p "$KD"
echo "==1=="
ai-memory identity generate --key-dir "$KD"
echo "==1rc==$?"
echo "==2=="
ai-memory identity generate --key-dir "$KD" 2>&1
echo "==2rc==$?"
echo "==3=="
ai-memory identity generate --key-dir "$KD" --force 2>&1
echo "==3rc==$?"
echo "==4=="
ai-memory identity generate --key-dir "$KD" --no-overwrite 2>&1
echo "==4rc==$?"
ls -1 "$KD" || true
rm -rf "$KD"
"""
    r = h.ssh_bash_script(node, script, timeout=60)
    out = r.stdout or ""
    log(out)

    def get_block(label: str) -> str:
        # find ==label== ... ==labelrc== boundaries
        start = out.find(f"=={label}==")
        end = out.find(f"=={label}rc==")
        if start < 0 or end < 0:
            return ""
        return out[start + len(f"=={label}==") : end]

    def get_rc(label: str) -> int:
        start = out.find(f"=={label}rc==")
        if start < 0:
            return -1
        nl = out.find("\n", start)
        try:
            return int(out[start + len(f"=={label}rc=="):nl].strip())
        except ValueError:
            return -1

    rc1, rc2, rc3, rc4 = get_rc("1"), get_rc("2"), get_rc("3"), get_rc("4")
    block2 = get_block("2")

    reasons: list[str] = []
    passed = True
    if rc1 != 0:
        passed = False; reasons.append(f"first generate rc={rc1} (expected 0)")
    if rc2 == 0:
        passed = False; reasons.append("second generate without --force succeeded (expected non-zero)")
    if "pass --force" not in block2.lower() and "--force" not in block2.lower():
        passed = False; reasons.append("second generate stderr did not mention --force")
    if rc3 != 0:
        passed = False; reasons.append(f"third generate --force rc={rc3} (expected 0)")
    if rc4 != 0:
        # legacy --no-overwrite is a hidden no-op; should NOT error
        passed = False; reasons.append(f"--no-overwrite rc={rc4} (expected 0; legacy no-op)")

    h.emit(passed=passed, reason="; ".join(reasons),
           rc=[rc1, rc2, rc3, rc4],
           second_invocation_msg=block2.strip()[:200], reasons=reasons)


if __name__ == "__main__":
    main()
