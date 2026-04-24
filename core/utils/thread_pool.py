from concurrent.futures import ThreadPoolExecutor
import traceback

class AppThreadPool:
    """Centralized thread manager to prevent runaway thread spawning."""
    def __init__(self, max_workers=10, logger=None):
        # 10 workers is a safe ceiling for an I/O heavy desktop app
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.logger = logger

    def submit(self, fn, *args, **kwargs):
        """Submits a task to the pool and attaches an error handler."""
        future = self.executor.submit(fn, *args, **kwargs)
        future.add_done_callback(self._handle_exception)
        return future

    def _handle_exception(self, future):
        """Silently catches and logs thread crashes so they don't take down the UI."""
        try:
            future.result()
        except Exception as e:
            if self.logger:
                self.logger(f"Thread Pool Exception: {e}\n{traceback.format_exc()}")
            else:
                print(f"Thread Pool Exception: {e}")

    def shutdown(self):
        """Cleanly kills all pending tasks."""
        self.executor.shutdown(wait=False, cancel_futures=True)