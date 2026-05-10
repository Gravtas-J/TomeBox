from core.utils.wake import keep
import pytest
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