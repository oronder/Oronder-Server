from datetime import datetime

import pytest

from utils import parse_time


@pytest.mark.parametrize(
    "text",
    ["8/12/26 9:00 PM", "8/12/26 9pm", "08/12/26 9:00 PM", "8/12/2026 9:00 PM"],
)
def test_two_digit_year_is_this_century_even_when_past(text):
    parsed, error = parse_time(text, "US/Central")
    assert error is None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 8, 12, 21)


def test_two_digit_year_in_the_future():
    parsed, _ = parse_time("12/25/45 8pm", "US/Central")
    assert (parsed.year, parsed.month, parsed.day) == (2045, 12, 25)


def test_four_digit_year_untouched():
    parsed, _ = parse_time("10/29/1929 9:30 AM", "US/Eastern")
    assert parsed.replace(tzinfo=None) == datetime(1929, 10, 29, 9, 30)


def test_garbage_reports_an_error():
    parsed, error = parse_time("not a date at all", "US/Central")
    assert parsed is None and "Unable to parse" in error
