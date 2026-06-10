import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor


class AppThreadPool:
    """Centralized thread manager with task throttling capabilities."""

    def __init__(self, max_workers=10, logger=None):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.logger = logger

        # Throttling state
        self._api_lock = threading.Lock()
        self._last_api_call = 0.0
        self.api_throttle_delay = 1.5  # Minimum seconds between API-bound tasks

    def submit(self, fn, *args, task_type="standard", **kwargs):
        """Submits a task. Use task_type='api' to enforce rate limit delays."""
        if task_type == "api":
            future = self.executor.submit(self._throttled_wrapper, fn, *args, **kwargs)
        else:
            future = self.executor.submit(fn, *args, **kwargs)

        future.add_done_callback(self._handle_exception)
        return future

    def _throttled_wrapper(self, fn, *args, **kwargs):
        """Blocks execution until the required throttle delay has passed."""
        with self._api_lock:
            now = time.time()
            elapsed = now - self._last_api_call
            if elapsed < self.api_throttle_delay:
                time.sleep(self.api_throttle_delay - elapsed)
            self._last_api_call = time.time()

        return fn(*args, **kwargs)

    def _handle_exception(self, future):
        try:
            future.result()
        except Exception as e:
            if self.logger:
                self.logger(f"Thread Pool Exception: {e}\n{traceback.format_exc()}")
            else:
                print(f"Thread Pool Exception: {e}")

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
