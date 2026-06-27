import pytest

from app.csv_parser import CSVValidationError, parse_csv


def csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_valid_full():
    rows, errors = parse_csv(csv("name,address,phone\nA,1 St,555\nB,2 St,556\n"), max_rows=20)
    assert errors == []
    assert [r.name for r in rows] == ["A", "B"]
    assert rows[0].phone == "555"


def test_missing_phone_column_ok():
    rows, errors = parse_csv(csv("name,address\nA,1 St\n"), max_rows=20)
    assert errors == []
    assert rows[0].phone is None


def test_blank_phone_value_ok():
    rows, errors = parse_csv(csv("name,address,phone\nA,1 St,\n"), max_rows=20)
    assert rows[0].phone is None


def test_missing_required_field_becomes_row_error():
    rows, errors = parse_csv(csv("name,address,phone\n,1 St,555\nB,2 St,556\n"), max_rows=20)
    assert len(rows) == 1
    assert len(errors) == 1
    assert errors[0].row == 1
    assert "name" in errors[0].error


def test_too_many_rows():
    body = "name,address\n" + "".join(f"H{i},addr\n" for i in range(21))
    with pytest.raises(CSVValidationError, match="Too many rows"):
        parse_csv(csv(body), max_rows=20)


def test_wrong_header():
    with pytest.raises(CSVValidationError, match="Missing required column"):
        parse_csv(csv("hospital,location\nA,B\n"), max_rows=20)


def test_unexpected_column():
    with pytest.raises(CSVValidationError, match="Unexpected column"):
        parse_csv(csv("name,address,phone,extra\nA,B,1,x\n"), max_rows=20)


def test_empty_file():
    with pytest.raises(CSVValidationError, match="empty"):
        parse_csv(csv("   "), max_rows=20)


def test_header_only():
    with pytest.raises(CSVValidationError, match="no data rows"):
        parse_csv(csv("name,address,phone\n"), max_rows=20)


def test_bom_tolerated():
    rows, errors = parse_csv("﻿name,address\nA,1 St\n".encode("utf-8"), max_rows=20)
    assert rows[0].name == "A"
