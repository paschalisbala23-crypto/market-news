"""
Market News fetcher.
Reads finance RSS feeds, tags each article by market, removes duplicates,
and saves everything to articles.json (rolling 7-day window).
"""

import hashlib
import html
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

socket.setdefaulttimeout(20)

OUTPUT = Path("articles.json")
KEEP_DAYS = 7
MAX_ARTICLES = 1500

# ---------------------------------------------------------------------------
# YOUR SOURCES. To add one, copy a line and change it.
# finance_only=True  -> keep everything from this feed
# finance_only=False -> keep only articles that match a finance keyword
# category           -> fallback tag when no keyword matches
# Some feeds may change or stop working over time. The script skips any
# feed that fails and prints a message in the run log.
# ---------------------------------------------------------------------------
FEEDS = [
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "category": "stocks", "finance_only": True},
    {"name": "CNBC Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "category": "markets", "finance_only": True},
    {"name": "CNBC Top News", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "category": "markets", "finance_only": False},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "category": "markets", "finance_only": True},
    {"name": "MarketWatch Pulse", "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "category": "markets", "finance_only": True},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "markets", "finance_only": True},
    {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news", "category": "forex", "finance_only": True},
    {"name": "investingLive", "url": "https://investinglive.com/feed", "category": "forex", "finance_only": True},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "crypto", "finance_only": True},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "category": "crypto", "finance_only": True},
    {"name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "category": "macro", "finance_only": True},
    {"name": "ECB", "url": "https://www.ecb.europa.eu/rss/press.html", "category": "macro", "finance_only": True},
]

# Keywords used to tag articles. An article can get several tags.
KEYWORDS = {
    "forex": ["forex", "fx", "currency", "currencies", "eur/usd", "eurusd", "gbp/usd", "usd/jpy",
              "usdjpy", "gbpusd", "dollar", "euro", "yen", "sterling", "swiss franc", "aussie"],
    "stocks": ["stock", "stocks", "shares", "earnings", "equities", "ipo", "dividend", "buyback",
               "wall street", "shareholders"],
    "crypto": ["bitcoin", "btc", "ethereum", "ether", "crypto", "cryptocurrency", "blockchain",
               "stablecoin", "solana", "xrp", "binance", "altcoin", "defi"],
    "etf": ["etf", "etfs", "exchange-traded", "exchange traded"],
    "options": ["options", "call option", "put option", "implied volatility", "options trading", "vix"],
    "futures": ["futures", "crude oil", "oil prices", "brent", "wti", "gold", "commodity",
                "commodities", "natural gas", "cme"],
    "bonds": ["bond", "bonds", "yield", "yields", "treasury", "treasuries", "gilt", "gilts",
              "bund", "fixed income"],
    "indices": ["s&p 500", "s&p", "dow jones", "dow", "nasdaq", "ftse", "dax", "nikkei", "stoxx",
                "russell", "index", "indices", "cac 40", "hang seng"],
    "macro": ["inflation", "fed", "federal reserve", "ecb", "interest rate", "interest rates",
              "rate cut", "rate hike", "gdp", "central bank", "jobs report", "cpi", "payrolls",
              "recession", "tariff", "tariffs", "boe", "boj"],
}

PATTERNS = {
    cat: re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in kws) + r")(?![a-z0-9])")
    for cat, kws in KEYWORDS.items()
}


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def snippet(raw: str, limit: int = 220) -> str:
    text = clean_text(raw)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def tag_article(title: str, summary: str):
    haystack = f"{title} {summary}".lower()
    return [cat for cat, pat in PATTERNS.items() if pat.search(haystack)]


def entry_date(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def article_id(link: str) -> str:
    key = link.split("?")[0].split("#")[0].rstrip("/").lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def load_existing() -> dict:
    if OUTPUT.exists():
        try:
            data = json.loads(OUTPUT.read_text(encoding="utf-8"))
            return {a["id"]: a for a in data.get("articles", [])}
        except Exception as exc:  # corrupted file, start fresh
            print(f"Could not read existing file: {exc}")
    return {}


def main():
    articles = load_existing()
    added = 0

    for feed in FEEDS:
        try:
            parsed = feedparser.parse(feed["url"], agent="MarketNewsBot/1.0")
            if not parsed.entries:
                print(f"[skip] {feed['name']}: no entries (feed may have changed)")
                continue
        except Exception as exc:
            print(f"[error] {feed['name']}: {exc}")
            continue

        count = 0
        for entry in parsed.entries:
            title = clean_text(entry.get("title", ""))
            link = entry.get("link", "")
            if not title or not link:
                continue

            summary = entry.get("summary", "") or entry.get("description", "")
            tags = tag_article(title, clean_text(summary))

            if not tags:
                if not feed["finance_only"]:
                    continue
                tags = [feed["category"]]

            # Put the feed's own category first if it is among the tags
            if feed["category"] in tags:
                tags.remove(feed["category"])
                tags.insert(0, feed["category"])

            art_id = article_id(link)
            if art_id in articles:
                continue

            articles[art_id] = {
                "id": art_id,
                "title": title,
                "link": link,
                "source": feed["name"],
                "snippet": snippet(summary),
                "categories": tags,
                "published": entry_date(entry).isoformat(),
            }
            added += 1
            count += 1
        print(f"[ok] {feed['name']}: {count} new")

    # Drop old articles
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    fresh = [a for a in articles.values() if datetime.fromisoformat(a["published"]) >= cutoff]

    # Newest first, then remove near-duplicate titles (same story, different outlet)
    fresh.sort(key=lambda a: a["published"], reverse=True)
    seen_titles = set()
    unique = []
    for a in fresh:
        key = title_key(a["title"])
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique.append(a)

    unique = unique[:MAX_ARTICLES]

    OUTPUT.write_text(
        json.dumps(
            {"updated": datetime.now(timezone.utc).isoformat(), "articles": unique},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"Done. {added} added, {len(unique)} total saved.")


if __name__ == "__main__":
    main()
