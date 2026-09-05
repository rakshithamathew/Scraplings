import logging
import sys
from pathlib import Path

from scrapling.spiders import Response, Spider


OUTPUT_PATH = Path(__file__).resolve().parents[1] / "output" / "quotes.json"


class QuotesSpider(Spider):
    name = "quotes"
    start_urls = ["https://quotes.toscrape.com/"]
    allowed_domains = {"quotes.toscrape.com"}

    # Keep this functionality test gentle on the single target domain.
    concurrent_requests = 2
    concurrent_requests_per_domain = 1
    download_delay = 0.5
    robots_txt_obey = True
    logging_level = logging.INFO

    async def parse(self, response: Response):
        for quote in response.css("div.quote"):
            yield {
                "text": quote.css("span.text::text").get(""),
                "author": quote.css("small.author::text").get(""),
            }

        next_page = response.css("li.next a::attr(href)").get()
        if next_page:
            yield response.follow(next_page, callback=self.parse)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = QuotesSpider().start()
    result.items.to_json(OUTPUT_PATH, indent=True)

    print(f"Pages crawled: {result.stats.requests_count}")
    print(f"Quotes collected: {result.stats.items_scraped}")
    print(f"Failed requests: {result.stats.failed_requests_count}")
    print(f"Crawl completed: {result.completed}")
    print(f"Output: {OUTPUT_PATH}")

    if not result.completed or result.stats.failed_requests_count:
        raise RuntimeError("The crawl did not complete successfully")


if __name__ == "__main__":
    main()
