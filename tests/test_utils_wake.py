from core.utils.wake import keep
import pytest
import sys
import importlib
import core.utils.wake


def test_wake_dummy_context_manager():
    """
    Verifies that 'keep.running()' functions as a valid context manager 
    regardless of whether wakepy is installed or the dummy fallback is used.
    """
    execution_flag = False
    
    try:
        with keep.running():
            execution_flag = True
    except Exception as e:
        pytest.fail(f"Context manager raised an unexpected exception: {e}")
        
    assert execution_flag is True

def test_wake_dummy_fallback(monkeypatch):
    """Forces an ImportError to guarantee the fallback KeepDummy is covered."""
    # Simulate wakepy not being installed
    monkeypatch.setitem(sys.modules, "wakepy", None)
    
    # Reload the module so it hits the ImportError block
    importlib.reload(core.utils.wake)
    
    keep = core.utils.wake.keep
    
    # Verify the dummy context manager works cleanly
    execution_flag = False
    try:
        with keep.running():
            execution_flag = True
    except Exception as e:
        pytest.fail(f"Dummy context manager failed: {e}")
        
    assert execution_flag is True
    
    # Restore the module to its normal state for the rest of the test suite
    monkeypatch.undo()
    importlib.reload(core.utils.wake)