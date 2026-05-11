import sys
import importlib
import pytest
from core.utils.text import format_series_list, normalize_title, find_matching_cloud_item
import core.utils.text

# --- format_series_list ---

@pytest.mark.parametrize("raw_series, expected", [
    ([{"title": "The Expanse", "sequence": "1"}], "The Expanse (Bk 1)"),
    ([{"title": "Standalone"}], "Standalone"),
    ([{"title": "Series A", "sequence": "1"}, {"title": "Series B", "sequence": "2"}], "Series A (Bk 1), Series B (Bk 2)"),
    ([{"title": "Valid"}, {"invalid": "data"}, "not_a_dict"], "Valid"),
    ([], ""),
    (None, ""),
])
def test_format_series_list(raw_series, expected):
    assert format_series_list(raw_series) == expected


# --- normalize_title ---

@pytest.mark.parametrize("input_title, expected", [
    ("The Martian", "the martian"),
    ("Dune (Unabridged)", "dune"),
    ("Project Hail Mary (Audible Audio Edition)", "project hail mary"),
    ("1984: A Novel", "1984"),
    ("Harry Potter and the Sorcerer's Stone, Book 1", "harry potter and the sorcerer s stone"),
    ("Foundation Vol. 1", "foundation"),
    ("“Smart Quotes” & ‘Apostrophes’", "smart quotes apostrophes"),
    ("   Extra    Spaces   ", "extra spaces"),
    (None, ""),
    ("", ""),
])
def test_normalize_title(input_title, expected):
    assert normalize_title(input_title) == expected


# --- find_matching_cloud_item ---

def test_find_matching_exact():
    cloud_items = [
        {"title": "The Hobbit", "asin": "123"},
        {"title": "Lord of the Rings", "asin": "456"}
    ]
    match = find_matching_cloud_item("The Hobbit", cloud_items)
    assert match is not None
    assert match["asin"] == "123"

def test_find_matching_fuzzy():
    cloud_items = [
        {"title": "Dungeon Crawler Carl", "asin": "111"},
        {"title": "Different Book", "asin": "222"}
    ]
    # Simulate a messy filename
    match = find_matching_cloud_item("Dungeon Crawler Carl [Soundtrack included]", cloud_items)
    assert match is not None
    assert match["asin"] == "111"

def test_find_matching_series_prefix():
    cloud_items = [
        {
            "title": "Leviathan Wakes",
            "asin": "333",
            "series": [{"title": "The Expanse", "sequence": "1"}]
        }
    ]
    # Filename includes the series name, which normally throws off standard fuzzy matching
    match = find_matching_cloud_item("The Expanse Book 1 - Leviathan Wakes", cloud_items)
    assert match is not None
    assert match["asin"] == "333"

def test_find_matching_no_match():
    cloud_items = [{"title": "Specific Book", "asin": "999"}]
    match = find_matching_cloud_item("Completely Unrelated Title", cloud_items)
    assert match is None

def test_find_matching_empty_inputs():
    assert find_matching_cloud_item(None, [{"title": "A"}]) is None
    assert find_matching_cloud_item("Title", []) is None
    assert find_matching_cloud_item("Title", None) is None

def test_format_series_list():
    """Verifies edge cases for the Audible series formatter."""
    # Null/Empty
    assert format_series_list(None) == ""
    assert format_series_list([]) == ""
    
    # Valid single entry
    assert format_series_list([{"title": "The Expanse", "sequence": "1"}]) == "The Expanse (Bk 1)"
    
    # Missing sequence should drop the suffix entirely
    assert format_series_list([{"title": "Standalone"}]) == "Standalone"
    
    # Ignore invalid types and items missing a title
    assert format_series_list(["string_entry", {"sequence": "2"}]) == ""
    
    # Multiple valid items
    assert format_series_list([
        {"title": "Series A", "sequence": "1"},
        {"title": "Series B"}
    ]) == "Series A (Bk 1), Series B"

def test_normalize_title():
    """Verifies punctuation, boilerplate, and suffix stripping."""
    # Null/Empty
    assert normalize_title(None) == ""
    assert normalize_title("") == ""
    
    # Fancy quotes normalization
    assert normalize_title("The \u201cGreat\u201d \u2018Book\u2019") == "the great book"
    
    # Suffix stripping
    assert normalize_title("Dune (Unabridged)") == "dune"
    assert normalize_title("Foundation (Audible Audio Edition)") == "foundation"
    assert normalize_title("Some Book: a novel") == "some book"
    
    # Series markers (Book N, Vol N)
    assert normalize_title("Harry Potter Book 1") == "harry potter"
    assert normalize_title("Lord of the Rings, Vol 2") == "lord of the rings"
    
    # Punctuation and whitespace collapsing
    assert normalize_title("A  Very,  Long: Title!") == "a very long title"

# --- Fuzzy Matching Tests ---

def test_find_matching_cloud_item_fuzz():
    """Verifies the rapidfuzz matching logic when the library is present."""
    core.utils.text.RAPIDFUZZ_AVAILABLE = True
    
    cloud_items = [
        {"title": "The Lord of the Rings: The Fellowship of the Ring", "asin": "1"},
        {"title": "Something completely different", "asin": "2"},
        {"title": "", "asin": "3"} # Tests empty cloud title skip logic
    ]
    
    # 1. Standard fuzzy match
    match = find_matching_cloud_item("Lord of the Rings Fellowship", cloud_items)
    assert match["asin"] == "1"
    
    # 2. Complete mismatch (falls below 85 threshold)
    assert find_matching_cloud_item("Harry Potter", cloud_items) is None
    
    # 3. Invalid inputs
    assert find_matching_cloud_item("", cloud_items) is None
    assert find_matching_cloud_item("Title", []) is None

def test_find_matching_cloud_item_series_aware():
    """Verifies the advanced 'series stripping' logic used for local file matches."""
    core.utils.text.RAPIDFUZZ_AVAILABLE = True
    
    cloud_items = [
        {
            "title": "The Eye of the World",
            "asin": "1",
            "series": [
                {"title": "The Wheel of Time"},
                "Invalid Series Entry", # Tests skip logic for non-dicts
                {"title": "Too"}        # Tests skip logic for titles < 4 chars
            ]
        }
    ]
    
    # Target starts with series name: "The Wheel of Time: The Eye of the World"
    # The algorithm should detect "The Wheel of Time", strip it, and match the rest perfectly
    match = find_matching_cloud_item("The Wheel of Time The Eye of the World", cloud_items)
    assert match["asin"] == "1"
    
    # If the target is EXACTLY the series name, stripping it leaves nothing. 
    # It should cleanly skip the series-aware check instead of crashing.
    assert find_matching_cloud_item("The Wheel of Time", cloud_items) is None

# --- Fallback & Import Trap Tests ---

def test_find_matching_cloud_item_fallback(monkeypatch):
    """Forces rapidfuzz off to verify the exact-match fallback algorithm."""
    monkeypatch.setattr(core.utils.text, "RAPIDFUZZ_AVAILABLE", False)
    
    cloud_items = [{"title": "Exact Match Title", "asin": "1"}]
    
    # Exact match works
    assert find_matching_cloud_item("Exact Match Title", cloud_items)["asin"] == "1"
    
    # Fuzzy match fails because rapidfuzz is offline
    assert find_matching_cloud_item("Exact Match", cloud_items) is None

def test_rapidfuzz_import_error(monkeypatch):
    """Simulates an uninstalled rapidfuzz package to hit the ImportError trap."""
    monkeypatch.setitem(sys.modules, "rapidfuzz", None)
    
    importlib.reload(core.utils.text)
    assert core.utils.text.RAPIDFUZZ_AVAILABLE is False
    
    # Cleanup to avoid breaking other tests
    monkeypatch.undo()
    importlib.reload(core.utils.text)

def test_find_matching_cloud_item_series_missing_title():
    core.utils.text.RAPIDFUZZ_AVAILABLE = True
    cloud_items = [{"title": "Cloud Book", "series": [{"sequence": "1"}]}] # Missing 'title'
    assert core.utils.text.find_matching_cloud_item("Target", cloud_items) is None