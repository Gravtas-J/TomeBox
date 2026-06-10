import csv

import pytest

from core.exporter import LibraryExporter


@pytest.fixture
def sample_data():
    local_library = {
        "/local/dungeon.m4b": {
            "title": "Dungeon Crawler Carl",
            "asin": "B08V8B2CGV",
            "format": "M4B",
            "path": "/local/dungeon.m4b",
        },
        "/local/unknown.mp3": {
            "title": "Local Only Podcast",
            "format": "MP3",
            "path": "/local/unknown.mp3",
        },
    }

    cloud_items = [
        {
            "title": "Dungeon Crawler Carl",
            "asin": "B08V8B2CGV",
            "authors": [{"name": "Matt Dinniman"}],
            "series": [{"title": "Dungeon Crawler Carl", "sequence": "1"}],
            "runtime_length_min": 805,
        },
        {
            "title": "=Cloud Only Book",  # Starting with '=' to test CSV injection sanitization
            "asin": "B012345678",
            "authors": [{"name": "Author"}],
            "runtime_length_min": 300,
        },
    ]
    return local_library, cloud_items


def test_csv_sanitization():
    assert LibraryExporter._sanitize_csv("Normal Title") == "Normal Title"
    assert LibraryExporter._sanitize_csv("=Inject") == "'=Inject"
    assert LibraryExporter._sanitize_csv("+Inject") == "'+Inject"
    assert LibraryExporter._sanitize_csv("-Inject") == "'-Inject"
    assert LibraryExporter._sanitize_csv("@Inject") == "'@Inject"
    assert LibraryExporter._sanitize_csv(123) == "123"


def test_export_csv(tmp_path, sample_data):
    local_lib, cloud_items = sample_data
    out_file = tmp_path / "export.csv"

    LibraryExporter.export_csv(str(out_file), local_lib, cloud_items)

    assert out_file.exists()

    with open(out_file, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))

        # Header + 2 cloud items + 1 local-only item
        assert len(reader) == 4

        headers = reader[0]
        assert headers[0] == "Title"

        # Check cloud item matched with local file
        carl_row = next(r for r in reader if "Dungeon Crawler Carl" in r[0])
        assert carl_row[4] == "B08V8B2CGV"  # ASIN
        assert carl_row[5] == "Downloaded (M4B)"  # Status
        assert carl_row[6] == "/local/dungeon.m4b"  # Path

        # Check CSV Injection prevention
        injected_row = next(r for r in reader if "Cloud Only Book" in r[0])
        assert injected_row[0] == "'=Cloud Only Book"  # Sanitized
        assert injected_row[5] == "Cloud Only"

        # Check local-only item
        local_row = next(r for r in reader if "Local Only Podcast" in r[0])
        assert local_row[1] == "Local File"
        assert local_row[5] == "Downloaded (MP3)"


def test_export_html(tmp_path, sample_data):
    local_lib, cloud_items = sample_data
    out_file = tmp_path / "export.html"

    LibraryExporter.export_html(str(out_file), local_lib, cloud_items)

    assert out_file.exists()

    with open(out_file, "r", encoding="utf-8") as f:
        content = f.read()

        # Basic HTML structure checks
        assert "<!DOCTYPE html>" in content
        assert "My TomeBox Library" in content

        # Cloud + Local merged check
        assert "Dungeon Crawler Carl" in content
        assert "Matt Dinniman" in content
        assert "Downloaded (M4B)" in content

        # HTML escaping check
        assert "=Cloud Only Book" in content  # Escaped '='

        # Local-only check
        assert "Local Only Podcast" in content
        assert "Downloaded (MP3)" in content


def test_exports_with_empty_library(tmp_path):
    csv_file = tmp_path / "empty.csv"
    html_file = tmp_path / "empty.html"

    LibraryExporter.export_csv(str(csv_file), {}, [])
    LibraryExporter.export_html(str(html_file), {}, [])

    assert csv_file.exists()
    assert html_file.exists()

    # Ensure headers still write to CSV
    with open(csv_file, "r", encoding="utf-8") as f:
        assert len(list(csv.reader(f))) == 1

    # Ensure HTML template compiles without data
    with open(html_file, "r", encoding="utf-8") as f:
        assert "</html>" in f.read()
