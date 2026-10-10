"""Subprocess execution with process-group isolation.

Every subprocess argocheck spawns (git, ssh-add, ssh-agent, helm) goes
through `run()` here instead of calling `subprocess.run` directly, so that:

- A `timeout=` actually kills the whole command tree, not just the direct
  child. `git clone` over SSH spawns `ssh`, which in interactive mode spawns
  our own askpass helper — killing only the `git` pid on timeout leaves `ssh`
  and the askpass helper (blocked on a `getpass()` read with no timeout of
  its own) running forever as orphans.
- A SIGTERM/SIGINT to the whole argocheck process reliably kills any
  currently-running subprocess tree instead of leaving it orphaned — which,
  for a hung passphrase prompt, would otherwise keep running (and holding the
  terminal) indefinitely even after argocheck itself has exited.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Any

_lock = threading.Lock()
_active_pgids: set[int] = set()


def run(cmd: list[str], *, timeout: float | None = None, check: bool = False, **kwargs: Any) -> subprocess.CompletedProcess:
    """Drop-in replacement for `subprocess.run`, plus process-group isolation
    (`start_new_session=True`) so the whole tree the command spawns can be
    killed as a unit, by a timeout here or by `kill_active()`."""
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    popen = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    pgid = popen.pid  # start_new_session means the child is its own group leader

    with _lock:
        _active_pgids.add(pgid)
    try:
        stdout, stderr = popen.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(pgid)
        popen.wait()
        raise
    finally:
        with _lock:
            _active_pgids.discard(pgid)

    result = subprocess.CompletedProcess(cmd, popen.returncode, stdout, stderr)
    if check and popen.returncode != 0:
        raise subprocess.CalledProcessError(popen.returncode, cmd, stdout, stderr)
    return result


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def kill_active() -> None:
    """Kill every subprocess tree currently running through `run()`. Safe to
    call from a signal handler or from another thread."""
    with _lock:
        pgids = list(_active_pgids)
    for pgid in pgids:
        _kill_group(pgid)


def _handle_terminate(signum: int, frame: Any) -> None:
    kill_active()
    raise SystemExit(128 + signum)


def install_signal_handlers() -> None:
    """Make SIGTERM/SIGINT kill any in-flight subprocess tree before the
    process exits, instead of leaving it orphaned. Call once, from a process
    that owns its own signal handling (the CLI) — not from the web server,
    which needs uvicorn's own SIGTERM handling for a graceful HTTP shutdown;
    see server.py's handle_exit wrapping instead."""
    signal.signal(signal.SIGTERM, _handle_terminate)
    signal.signal(signal.SIGINT, _handle_terminate)
