import time
import pytest
from unittest.mock import MagicMock
from core.utils.thread_pool import AppThreadPool

def test_standard_submission():
    """Verifies that standard tasks run normally and return their results."""
    pool = AppThreadPool(max_workers=1)
    
    def multiply(x, y):
        return x * y
        
    future = pool.submit(multiply, 5, 4)
    assert future.result() == 20
    
    pool.shutdown()

def test_api_throttling():
    """Verifies that the pool enforces the delay between 'api' type tasks."""
    pool = AppThreadPool(max_workers=2)
    
    # Shrink the delay so the test runs fast, but keep it measurable
    test_delay = 0.1
    pool.api_throttle_delay = test_delay
    
    # Submit two rapid API tasks
    f1 = pool.submit(time.time, task_type="api")
    f2 = pool.submit(time.time, task_type="api")
    
    t1 = f1.result()
    t2 = f2.result()
    
    # The second task must have executed at least `test_delay` seconds after the first
    assert (t2 - t1) >= test_delay
    
    pool.shutdown()

def test_standard_tasks_bypass_throttle():
    """Verifies that standard tasks don't get trapped by the API lock."""
    pool = AppThreadPool(max_workers=2)
    pool.api_throttle_delay = 1.0  # Large delay
    
    # Start an API task that will reset the timer
    pool.submit(time.time, task_type="api").result()
    
    # A standard task submitted immediately after should run instantly
    start = time.time()
    pool.submit(time.time).result()
    duration = time.time() - start
    
    # It bypassed the 1.0s delay
    assert duration < 0.1
    
    pool.shutdown()

def test_exception_logging():
    """Verifies that background thread crashes are caught and logged."""
    mock_logger = MagicMock()
    pool = AppThreadPool(max_workers=1, logger=mock_logger)
    
    def crash():
        raise ValueError("Simulated thread crash")
        
    future = pool.submit(crash)
    
    # Wait for the future to register the exception
    exception = future.exception()
    assert isinstance(exception, ValueError)
    
    # Wait a tiny fraction of a second for the add_done_callback to execute
    time.sleep(0.05)
    
    # Verify the callback passed the stack trace to the logger
    assert mock_logger.call_count == 1
    log_msg = mock_logger.call_args[0][0]
    assert "Thread Pool Exception" in log_msg
    assert "Simulated thread crash" in log_msg
    
    pool.shutdown()