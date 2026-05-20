import subprocess
import os
import sys
import signal
import ctypes
import atexit

# ─── Windows: Job Object (kill-on-close) ──────────────────────────────────────

_job_handle = None  # module-level singleton; kernel closes it on process death

def _init_windows_job():
    """Create a Job Object that kills all assigned children when the parent dies."""
    global _job_handle
    if _job_handle is not None:
        return _job_handle

    from ctypes import wintypes

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation  = 9

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount',  'WriteTransferCount',  'OtherTransferCount',
        )]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
            ('PerJobUserTimeLimit',     wintypes.LARGE_INTEGER),
            ('LimitFlags',              wintypes.DWORD),
            ('MinimumWorkingSetSize',   ctypes.c_size_t),
            ('MaximumWorkingSetSize',   ctypes.c_size_t),
            ('ActiveProcessLimit',      wintypes.DWORD),
            ('Affinity',                ctypes.c_size_t),
            ('PriorityClass',           wintypes.DWORD),
            ('SchedulingClass',         wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ('IoInfo',                IO_COUNTERS),
            ('ProcessMemoryLimit',    ctypes.c_size_t),
            ('JobMemoryLimit',        ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed',     ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateJobObjectW.restype  = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

    handle = k32.CreateJobObjectW(None, None)
    if not handle:
        return None

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    k32.SetInformationJobObject.restype  = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    if not k32.SetInformationJobObject(
        handle, JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        k32.CloseHandle(handle)
        return None

    _job_handle = handle
    # Defensive: also close on normal interpreter exit (not strictly required
    # since the kernel cleans up handles on process termination).
    atexit.register(lambda: k32.CloseHandle(handle))
    return handle


def _assign_pid_to_job(pid):
    """Assign a freshly-spawned process to the kill-on-close job."""
    handle = _init_windows_job()
    if not handle:
        return False

    from ctypes import wintypes
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenProcess.restype  = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    proc = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not proc:
        return False
    try:
        k32.AssignProcessToJobObject.restype  = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(handle, proc))
    finally:
        k32.CloseHandle(proc)


# ─── Linux: PR_SET_PDEATHSIG ──────────────────────────────────────────────────

def _linux_pdeathsig_preexec():
    """preexec_fn: tell the kernel to SIGTERM this child when the parent dies."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass  # best effort; non-glibc Linux or weird envs fall through


# ─── Public API ───────────────────────────────────────────────────────────────

class ProcessRunner:
    """A centralized utility for executing external binaries safely."""

    @staticmethod
    def get_creation_flags():
        return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    @staticmethod
    def run_blocking(cmd, capture_output=True, check=False, **kwargs):
        kwargs.pop('text', None)
        kwargs.pop('encoding', None)
        kwargs.pop('creationflags', None)
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=ProcessRunner.get_creation_flags(),
            check=check,
            **kwargs,
        )

    @staticmethod
    def run_async(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs):
        """Spawns a long-running child tied to the parent's lifetime."""
        kwargs.pop('creationflags', None)
        kwargs.pop('preexec_fn', None)

        preexec_fn = _linux_pdeathsig_preexec if sys.platform.startswith('linux') else None

        proc = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            creationflags=ProcessRunner.get_creation_flags(),
            preexec_fn=preexec_fn,
            **kwargs,
        )

        if os.name == 'nt':
            try:
                _assign_pid_to_job(proc.pid)
            except Exception:
                pass  # best effort — don't break playback if the API call fails

        return proc