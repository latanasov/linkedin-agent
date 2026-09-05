"""Process liveness, shared by the heartbeat, the task queue and `stop`."""

from __future__ import annotations

import os


def pid_alive(pid: int | None) -> bool:
    """Is a process with this id still doing anything?

    A zombie counts as gone: a run loop whose parent never reaped it (one started from
    an assistant's tool shell, say) would otherwise look active forever, and `stop` would
    wait on a process that has already exited. psutil is used when it can answer; a
    plain signal-0 probe is the fallback."""
    if not pid or pid <= 0:
        return False
    try:
        import psutil

        proc = psutil.Process(pid)
        return bool(proc.status() != psutil.STATUS_ZOMBIE)
    except ImportError:
        pass
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
    except Exception:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
