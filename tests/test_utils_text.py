import pytest
from core.utils.text import format_series_list, normalize_title, find_matching_cloud_item

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