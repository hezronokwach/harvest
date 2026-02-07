import pytest
from agent_new import truncate_text, normalize_content_for_llm

def test_truncate_text_string():
    assert truncate_text("hello world", 5) == "hello"

def test_truncate_text_none():
    assert truncate_text(None) == ""

def test_truncate_text_list():
    # Helper should join and then truncate
    class MockMsg:
        def __init__(self, c):
            self.content = c
    msgs = [MockMsg("hello"), MockMsg("world")]
    res = truncate_text(msgs, 5)
    assert res == "hello"

def test_normalize_content_str():
    assert normalize_content_for_llm("test") == ["test"]

def test_normalize_content_list():
    assert normalize_content_for_llm(["a", "b"]) == ["a", "b"]

def test_normalize_content_slice_bug():
    # This is the bug the user reported
    s = slice(None, 300, None)
    res = normalize_content_for_llm(s)
    assert isinstance(res, list)
    assert all(isinstance(x, str) for x in res)
    assert res == [""] # Helper should convert slice to empty string safely

def test_normalize_content_int():
    assert normalize_content_for_llm(123) == ["123"]
