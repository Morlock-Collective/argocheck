"""Tests for process-group isolation and signal-triggered cleanup in proc.py."""
import os
import signal
import subprocess
import time

import pytest

from argocheck import proc


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_returns_normally_on_success():
    result = proc.run(["true"])
    assert result.returncode == 0


def test_run_check_raises_called_process_error():
    with pytest.raises(subprocess.CalledProcessError):
        proc.run(["false"], check=True)


def test_run_timeout_kills_whole_process_group_not_just_direct_child(tmp_path):
    """A timeout must kill the child's own children too (e.g. git -> ssh ->
    askpass), not just the direct subprocess — otherwise a grandchild blocked
    on an unanswered prompt is orphaned and keeps running forever."""
    pid_file = tmp_path / "grandchild-pid"
    # The shell is the direct child; "sleep 5" is its child. Both must die.
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run(["sh", "-c", f"sleep 5 & echo $! > {pid_file}; wait"], timeout=0.3)

    time.sleep(0.2)
    grandchild_pid = int(pid_file.read_text().strip())
    assert not _process_alive(grandchild_pid)


def test_run_cleans_up_active_registry_after_completion():
    proc.run(["true"])
    assert proc._active_pgids == set()


def test_kill_active_terminates_a_long_running_subprocess():
    """Simulates what the SIGTERM handler does: kill_active() must stop a
    subprocess that's still blocked (e.g. a hung passphrase prompt) without
    waiting for its own timeout."""
    import threading

    result_holder = {}

    def worker():
        try:
            proc.run(["sleep", "30"])
            result_holder["returncode"] = 0
        except Exception as e:
            result_holder["error"] = e

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.3)  # let the subprocess actually start and register
    assert len(proc._active_pgids) == 1
    pgid = next(iter(proc._active_pgids))

    proc.kill_active()
    t.join(timeout=5)

    assert not t.is_alive()
    assert not _process_alive(pgid)


def test_handle_terminate_kills_active_and_raises_system_exit():
    import threading

    def worker():
        try:
            proc.run(["sleep", "30"])
        except Exception:
            pass

    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.3)
    assert len(proc._active_pgids) == 1
    pgid = next(iter(proc._active_pgids))

    with pytest.raises(SystemExit):
        proc._handle_terminate(signal.SIGTERM, None)

    t.join(timeout=5)
    assert not _process_alive(pgid)
