"""Contract tests for stall-guard ``ps`` parsing (sumipan/nexus#6181)."""

from __future__ import annotations

from ghdag.core.stall_guard import (
    find_stalled_state_readers,
    parse_ps_output,
    scan_process_tree,
)

# Anonymized snapshots from Linux (GNU ps) and macOS during socketpair stall scenarios.
_LINUX_PS_SNAPSHOT = """\
    100       1 00:00:00 bash            bash -c exec 3<&5; snap=$(command cat <&3); echo DONE
    200     100 00:00:00 bash            bash -c exec 3<&5; snap=$(command cat
    300     200 00:00:00 cat             cat
"""

_DARWIN_PS_SNAPSHOT = """\
  100     1   0:00.00 bash             bash -c exec 3<&5; snap=$(command cat <&3); echo DONE
  200   100   0:00.00 bash             bash -c exec 3<&5; snap=$(command cat <&3); echo DONE
  300   200   0:00.00 cat              cat
"""

_ROOT_PID = 100
_STALLED_CAT_PID = 300


def test_linux_and_macos_ps_snapshots_identify_stalled_cat() -> None:
    for label, snapshot in (("linux", _LINUX_PS_SNAPSHOT), ("darwin", _DARWIN_PS_SNAPSHOT)):
        tree = scan_process_tree(_ROOT_PID, snapshot)
        all_procs = parse_ps_output(snapshot)
        stalled = find_stalled_state_readers(tree, all_procs=all_procs)
        assert stalled == [_STALLED_CAT_PID], label
