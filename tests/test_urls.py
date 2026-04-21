from app.vk.urls import parse_group_ref, parse_group_refs


def test_parse_full_url():
    r = parse_group_ref("https://vk.com/durov")
    assert r and r.screen_name == "durov" and r.vk_id is None


def test_parse_mobile_url():
    r = parse_group_ref("https://m.vk.com/team")
    assert r and r.screen_name == "team"


def test_parse_numeric_club():
    r = parse_group_ref("club1")
    assert r and r.vk_id == 1 and r.screen_name is None


def test_parse_public_url():
    r = parse_group_ref("https://vk.com/public123")
    assert r and r.vk_id == 123


def test_parse_screen_name_plain():
    r = parse_group_ref("team")
    assert r and r.screen_name == "team"


def test_parse_at_prefix():
    r = parse_group_ref("@durov")
    assert r and r.screen_name == "durov"


def test_parse_trailing_slash_and_path():
    r = parse_group_ref("https://vk.com/durov/?some=query")
    assert r and r.screen_name == "durov"


def test_parse_multi():
    refs = parse_group_refs("""
        https://vk.com/durov
        club1
        @team
        club1
    """)
    keys = [r.key for r in refs]
    assert keys == ["durov", "club1", "team"]


def test_parse_garbage_returns_none():
    assert parse_group_ref("not a url") is None
    assert parse_group_ref("") is None
