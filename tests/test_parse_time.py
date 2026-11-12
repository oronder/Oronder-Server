from datetime import datetime

import pytest

from utils import parse_time

THIS_YEAR = datetime.now().year
CENTURY = THIS_YEAR // 100 * 100
# A two digit year that is already over: last year's.
LAST_YY = f"{(THIS_YEAR - 1) % 100:02d}"


@pytest.mark.parametrize(
    "text",
    [
        f"8/12/{LAST_YY} 9:00 PM",
        f"8/12/{LAST_YY} 9pm",
        f"08/12/{LAST_YY} 9:00 PM",
    ],
)
def test_past_two_digit_year_stays_in_this_century(text):
    """The reported bug: a past date typed with a two digit year was pushed a
    hundred years into the future."""
    parsed, error = parse_time(text, "US/Central")
    assert error is None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (
        THIS_YEAR - 1,
        8,
        12,
        21,
    )


def test_future_two_digit_year_is_this_century():
    parsed, _ = parse_time("12/25/99 8pm", "US/Central")
    assert (parsed.year, parsed.month, parsed.day) == (CENTURY + 99, 12, 25)


def test_four_digit_year_untouched():
    parsed, _ = parse_time("10/29/1929 9:30 AM", "US/Eastern")
    assert parsed.replace(tzinfo=None) == datetime(1929, 10, 29, 9, 30)


def test_garbage_reports_an_error():
    parsed, error = parse_time("not a date at all", "US/Central")
    assert parsed is None and "Unable to parse" in error
