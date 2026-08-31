"""Stopping a service must actually stop it, and a stray one must not block.

Two regressions, both introduced by fixes that were themselves correct, and
both of which left Kendra alive but unable to hear:

  1. Voice stopped being detached, so it inherits the desktop app's microphone
     permission. But `_terminate` killed by PROCESS GROUP, which only works
     for a service that leads its own -- so `dev stop` silently stopped
     stopping voice.
  2. The survivor kept `voice.sock`. The single-instance guard then refused
     every restart ("a live service already owns it"), 91 times, while the
     supervisor retried every fifteen seconds. Ten services reported healthy
     and she could not hear a word.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

from kendra.devstack import CORE_SERVICES, OPTIONAL_SERVICES, DevStack

ROOT = Path(__file__).resolve().parents[1]


def test_terminate_does_not_assume_a_service_leads_its_own_group() -> None:
    source = inspect.getsource(DevStack._signal)
    assert "getpgid" in source, "it must check before using killpg"
    assert "os.kill(pid, sig)" in source, "an attached process needs a direct signal"
    # And _terminate must go through it rather than calling killpg itself.
    terminate = inspect.getsource(DevStack._terminate)
    assert "killpg" not in terminate
    assert "_signal(" in terminate


def test_a_process_that_leads_no_group_is_still_signalled() -> None:
    """The exact shape of the bug: pid != pgid, so killpg targeted nothing.

    Measured on the live stack: voice pid 18350 had pgid 12709, so
    `killpg(18350)` raised ProcessLookupError and was swallowed.
    """
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert os.getpgid(child.pid) != child.pid, "child should share our group"
        DevStack._signal(child.pid, 15)
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.poll() is not None, "an attached process was not signalled"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_a_leader_still_gets_its_whole_group() -> None:
    """Detached services must keep dying as a group, children included."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        assert os.getpgid(child.pid) == child.pid
        DevStack._signal(child.pid, 15)
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_orphans_are_reaped_before_a_start() -> None:
    """A service the state file has lost track of still owns its socket."""
    source = inspect.getsource(DevStack.start)
    assert "reap_orphans()" in source, "a stale owner would block the start"
    assert inspect.getsource(DevStack.stop).count("reap_orphans()") == 1, (
        "`dev stop` must mean stopped, including what it never recorded"
    )


def test_reaping_only_ever_targets_this_profile_and_its_services() -> None:
    """It kills by command line, so the matching has to be exact."""
    source = inspect.getsource(DevStack.reap_orphans)
    assert '"-m kendra" not in line' in source
    assert '" service " not in line' in source
    assert "marker not in line" in source, "must be scoped to this config file"
    assert "pid == os.getpid()" in source, "must never signal itself"
    known = {service.name for service in CORE_SERVICES + OPTIONAL_SERVICES}
    assert "voice" in known and "brain" in known


def test_voice_stays_attached_for_the_microphone() -> None:
    """The reason _terminate had to change in the first place."""
    source = inspect.getsource(DevStack.start)
    assert 'service.name != "voice"' in source
    assert "start_new_session=detached" in source
