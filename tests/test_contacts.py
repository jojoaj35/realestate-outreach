from contacts import normalize_phone, same_number


def test_normalize_us_formats():
    for raw in ["(210) 555-0123", "210-555-0123", "+1 210 555 0123", "12105550123"]:
        assert normalize_phone(raw) == "+12105550123"


def test_normalize_rejects_garbage():
    assert normalize_phone("") == ""
    assert normalize_phone("not a phone") == ""
    assert normalize_phone("123") == ""


def test_same_number_across_formats():
    assert same_number("(210) 555-0123", "+12105550123")
    assert not same_number("(210) 555-0123", "(210) 555-9999")
