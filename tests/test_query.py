from app.search.query import parse_query


def test_morph_default():
    pq = parse_query("автомобіль швидко")
    assert pq.mode == "morph"
    assert pq.terms == ["автомобіль", "швидко"]


def test_phrase_mode():
    pq = parse_query('"генеральний директор"')
    assert pq.mode == "phrase"
    assert pq.terms == ["генеральний", "директор"]


def test_substring_mode():
    pq = parse_query("~ауто~")
    assert pq.mode == "substring"
    assert pq.terms == ["ауто"]


def test_empty():
    pq = parse_query("  ")
    assert pq.is_empty
    assert pq.mode == "morph"
