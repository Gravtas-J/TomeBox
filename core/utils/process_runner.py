import subprocess
import os

class ProcessRunner:
    """A centralized utility for executing external binaries safely."""
    
    @staticmethod
    def get_creation_flags():
        """Returns the appropriate OS flags to hide background terminal windows."""
        return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    @staticmethod
    def run_blocking(cmd, capture_output=True, check=False, **kwargs):
        """Executes a command and waits for it to finish (e.g., FFprobe, single FFmpeg tasks)."""
        
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
            **kwargs
        )

    @staticmethod
    def run_async(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs):
        """Spawns a process and returns the Popen object without blocking (e.g., FFplay)."""
        return subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            creationflags=ProcessRunner.get_creation_flags(),
            **kwargs
        )