import os

import pytest
import requests


@pytest.mark.skipif(os.getenv("RUN_LOAD_TEST", "").strip() != "1", reason="set RUN_LOAD_TEST=1 to run")
def test_concurrent_queries_smoke():
    """
    Load test nhẹ (5-10 concurrent queries) theo kế hoạch bước 3b.
    Test này yêu cầu backend đang chạy thật ở BASE_URL.
    """
    base = (os.getenv("BASE_URL") or "http://localhost:8080").rstrip("/")

    def single_query():
        r = requests.post(f"{base}/query", json={"q": "test", "sources": [], "use_memory_tree": False}, timeout=10)
        assert r.status_code in (202, 429)
        return r.status_code

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda _i: single_query(), range(10)))

    assert all(code in (202, 429) for code in results)

