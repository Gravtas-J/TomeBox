from core.utils.text import format_series_list

# (description, input, expected output)
CASES = [
    ("entry with a sequence number",
     [{"title": "Stormlight", "sequence": "1"}],
     "Stormlight (Bk 1)"),

    ("entry with no sequence key",
     [{"title": "Stormlight"}],
     "Stormlight"),

    ("entry with empty-string sequence",
     [{"title": "Stormlight", "sequence": ""}],
     "Stormlight"),

    ("multiple entries — one with seq, one without",
     [{"title": "Mistborn", "sequence": "1"}, {"title": "Cosmere"}],
     "Mistborn (Bk 1), Cosmere"),

    ("None as input",
     None,
     ""),

    ("entries with no title are skipped",
     [{"sequence": "1"}, {"title": "Real"}],
     "Real"),
]


def test_format_series_list():
    """Pytest entry point — runs every case."""
    for name, input_data, expected in CASES:
        actual = format_series_list(input_data)
        assert actual == expected, f"{name}: expected {expected!r}, got {actual!r}"


if __name__ == "__main__":
    import sys

    print("format_series_list — running test cases\n")

    failures = 0
    for name, input_data, expected in CASES:
        actual = format_series_list(input_data)
        passed = actual == expected
        marker = "✓" if passed else "✗"

        print(f"  {marker} {name}")
        print(f"      input:    {input_data!r}")
        print(f"      output:   {actual!r}")
        if not passed:
            print(f"      expected: {expected!r}")
            failures += 1
        print()

    if failures == 0:
        print(f"All {len(CASES)} cases passed.")
    else:
        print(f"{failures} of {len(CASES)} cases failed.")
        sys.exit(1)