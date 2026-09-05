import sys

from scrapling.fetchers import DynamicFetcher


URL = "https://quotes.toscrape.com/"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # DynamicFetcher.fetch is a one-off request: it opens a DynamicSession and
    # closes its pages, browser context, browser, and Playwright on return.
    response = DynamicFetcher.fetch(
        URL,
        headless=True,
        wait_selector=".quote",
        wait_selector_state="visible",
        timeout=30_000,
    )

    quotes = list(response.css(".quote"))
    if len(quotes) < 5:
        raise RuntimeError(f"Expected at least five quotes, found {len(quotes)}")

    for number, quote in enumerate(quotes[:5], start=1):
        text = quote.css(".text::text").get("")
        author = quote.css(".author::text").get("")
        print(f"{number}. {text} — {author}")


if __name__ == "__main__":
    main()
