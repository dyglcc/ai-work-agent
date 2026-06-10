import pytest

from app.core.tools import _parse_duckduckgo_html, format_search_results


def test_parse_duckduckgo_html_result():
    html = '''
    <div class="result results_links_deep web-result">
      <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Frate">USD to CNY Exchange Rate</a>
      <a class="result__snippet">1 USD = 7.1234 CNY today</a>
    </div>
    '''

    results = _parse_duckduckgo_html(html, 5)

    assert len(results) == 1
    assert results[0].title == "USD to CNY Exchange Rate"
    assert results[0].url == "https://example.com/rate"
    assert "7.1234" in results[0].snippet


def test_format_search_results():
    text = format_search_results([
        {"title": "A", "url": "https://example.com", "snippet": "hello"}
    ])

    assert "1. A" in text
    assert "https://example.com" in text
    assert "hello" in text
