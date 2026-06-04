import pytest
import tkinter as tk
from unittest.mock import MagicMock, patch
from core.utils.image_cache import ImageCache

@pytest.fixture(scope="module")
def tk_root():
    """Tkinter PhotoImage requires a root window to exist."""
    root = tk.Tk()
    yield root
    root.destroy()

def test_image_cache_initialization(tk_root):
    cache = ImageCache(max_size=50)
    assert cache.max_size == 50
    assert len(cache.cache) == 0

def test_dummy_card_fallback(tk_root):
    """Verifies that a missing file safely generates a text-based dummy card."""
    cache = ImageCache(max_size=10)
    
    photo = cache.get_thumbnail(
        asin="DUMMY_123", 
        filepath="/path/that/does/not/exist.jpg", 
        title="Test Book", 
        author="Test Author", 
        size=(200, 200)
    )
    
    assert photo is not None
    assert "DUMMY_123_200x200" in cache.cache

def test_lru_eviction(tk_root):
    """Verifies that scrolling past the max_size evicts the oldest images from memory."""
    cache = ImageCache(max_size=3)
    
    # Load 4 items into a cache that only holds 3
    cache.get_thumbnail("ASIN_1", None, "A", "B", (200, 200))
    cache.get_thumbnail("ASIN_2", None, "A", "B", (200, 200))
    cache.get_thumbnail("ASIN_3", None, "A", "B", (200, 200))
    cache.get_thumbnail("ASIN_4", None, "A", "B", (200, 200))
    
    assert len(cache.cache) == 3
    
    # ASIN_1 was the oldest and should have been evicted to save RAM
    assert "ASIN_1_200x200" not in cache.cache
    assert "ASIN_4_200x200" in cache.cache

def test_lru_refresh_on_access(tk_root):
    """Verifies that accessing an old item marks it as new so it isn't evicted."""
    cache = ImageCache(max_size=3)
    
    cache.get_thumbnail("ASIN_1", None, "A", "B", (200, 200))
    cache.get_thumbnail("ASIN_2", None, "A", "B", (200, 200))
    cache.get_thumbnail("ASIN_3", None, "A", "B", (200, 200))
    
    # Access ASIN_1 again, moving it to the "most recently used" position
    cache.get_thumbnail("ASIN_1", None, "A", "B", (200, 200))
    
    # Add a 4th item, which forces an eviction
    cache.get_thumbnail("ASIN_4", None, "A", "B", (200, 200))
    
    # Because we accessed ASIN_1, ASIN_2 is now the oldest and should be evicted
    assert "ASIN_1_200x200" in cache.cache
    assert "ASIN_2_200x200" not in cache.cache