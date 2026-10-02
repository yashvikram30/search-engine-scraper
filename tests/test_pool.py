import pytest

from ddg_scraper.net import PoolExhaustedError, ProxyPool

URLS = ["http://a:1", "http://b:1"]

def test_spreads_requests_and_paces_each_proxy():
    pool = ProxyPool(URLS, min_interval=1.0)
    first, wait1 = pool.acquire()
    second, wait2 = pool.acquire()
    assert first is not second
    assert wait1 == 0 and wait2 == 0

    _, wait3 = pool.acquire()  # both proxies are busy now
    assert wait3 > 0

def test_quarantines_a_blocked_proxy_and_raises_when_none_are_left():
    pool = ProxyPool(URLS, min_interval=0.01, cooldown=60)
    a, _ = pool.acquire()
    pool.report(a, "blocked")
    assert pool.cooling_count() == 1
    b, _ = pool.acquire()
    assert b is not a

    pool.report(b, "blocked")
    with pytest.raises(PoolExhaustedError):
        pool.acquire()

def test_three_errors_in_a_row_cool_a_proxy_down():
    pool = ProxyPool(URLS, min_interval=0.01, cooldown=60)
    a, _ = pool.acquire()
    for _ in range(3):
        pool.report(a, "error")
    assert pool.cooling_count() == 1

def test_direct_mode_never_quarantines():
    pool = ProxyPool([], min_interval=0.01)
    e, _ = pool.acquire()
    pool.report(e, "blocked")
    assert pool.cooling_count() == 0
    pool.acquire()  # does not raise
