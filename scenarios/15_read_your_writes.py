#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 15 — Read-your-writes.

alice writes on node-1 and immediately reads back from the same node.
No settle required — this is local durability, not federation. If this
fails, the local write-then-read guarantee is broken.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "15"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario15-ryw"
    marker = new_uuid("ryw-")

    log(f"alice writes + immediately reads M1 on node-1 (uuid={marker})")
    h.write_memory(h.node1_ip, "ai:alice", ns, title="ryw", content=marker)
    # No settle — same-node, same-process read must reflect the write.
    hit = h.count_matching(h.node1_ip, ns, content_equals=marker, limit=20)
    log(f"  alice sees {hit} (expected 1) immediately after write")

    reasons: list[str] = []
    passed = hit >= 1
    if not passed:
        reasons.append("writer did not see own write immediately on same node")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        uuid=marker,
        writer_sees_own_write=hit,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
