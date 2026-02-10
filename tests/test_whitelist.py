from nanobot.utils.whitelist import parse_str_list, to_set


def test_parse_str_list_json_array() -> None:
    assert parse_str_list('["a", "b"]') == ["a", "b"]


def test_parse_str_list_comma_or_space() -> None:
    assert parse_str_list("a,b  c") == ["a", "b", "c"]


def test_parse_str_list_wrapped_quotes() -> None:
    assert parse_str_list("'a,b'") == ["a", "b"]
    # Typical .env/shell pattern: wrap JSON in single quotes.
    assert parse_str_list('\'["x", "y"]\'') == ["x", "y"]


def test_to_set_normalizes() -> None:
    assert to_set([" a ", "", "b", "a"]) == {"a", "b"}
