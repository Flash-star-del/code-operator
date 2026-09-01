from greeting import greeting


def test_greeting_uses_chinese_salutation() -> None:
    assert greeting("小明") == "你好，小明！"
