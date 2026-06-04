import sys
import types


# Provide lightweight stubs for heavy external deps so tests can import modules
req = types.ModuleType("requests")
class HTTPError(Exception):
    pass
req.exceptions = types.SimpleNamespace(HTTPError=HTTPError)
req.get = lambda *a, **k: types.SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None)
req.HTTPError = HTTPError
req.Response = types.SimpleNamespace
sys.modules["requests"] = req

bs4 = types.ModuleType("bs4")
def BeautifulSoup(text, parser):
    return types.SimpleNamespace(find_all=lambda *a, **k: [], find=lambda *a, **k: None, get_text=lambda *a, **k: "")
bs4.BeautifulSoup = BeautifulSoup
sys.modules["bs4"] = bs4

ddgs = types.ModuleType("ddgs")
class DummyDDGS:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def text(self, *a, **k):
        return []
ddgs.DDGS = DummyDDGS
sys.modules["ddgs"] = ddgs
duck = types.ModuleType("duckduckgo_search")
duck.DDGS = DummyDDGS
sys.modules["duckduckgo_search"] = duck

# Stub internal modules referenced at import time
mock_tailor = types.ModuleType("job_search_agent.tailor")
mock_tailor.AdaptiveDocumentTailor = type("T", (), {})
sys.modules["job_search_agent.tailor"] = mock_tailor
mock_mem = types.ModuleType("job_search_agent.memory_clerk")
mock_mem.PersistentMemoryClerk = type("C", (), {"mark_approved": lambda self, x: None, "mark_rejected": lambda self, *a: None, "mark_applied": lambda self, *a: None})
sys.modules["job_search_agent.memory_clerk"] = mock_mem


def test_parse_json_list():
    from job_search_agent.review_helpers import _parse_json_list

    assert _parse_json_list('["a", "b"]') == ["a", "b"]
    assert _parse_json_list("") == []
    assert _parse_json_list("not a json") == ["not a json"]


def test_looks_like_job_url_and_parse_datetime():
    from job_search_agent.search import StrictSifter
    s = StrictSifter()
    assert s._looks_like_job_url("https://example.com/jobs/123", "", "") is True
    assert s._looks_like_job_url("https://example.com/about", "senior engineer", "") is False
    dt = StrictSifter._parse_datetime("2023-01-01T00:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
