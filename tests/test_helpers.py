import pytest

from helpers import fmt_money, parse_date, parse_decimal, parse_money


@pytest.mark.parametrize("text,cents", [("1.234,56", 123456), ("1234.56", 123456), ("12", 1200),
                                        ("0,99 €", 99), ("", 0), ("-5,00", -500)])
def test_parse_money(text, cents):
    assert parse_money(text) == cents


def test_parse_money_invalid():
    with pytest.raises(ValueError):
        parse_money("abc")


def test_fmt_money():
    assert fmt_money(123456) == "1.234,56 €"
    assert fmt_money(-99, currency=False) == "-0,99"


def test_parse_date_and_decimal():
    assert str(parse_date("01.10.2026")) == "2026-10-01"
    assert str(parse_decimal("2,5")) == "2.5"
