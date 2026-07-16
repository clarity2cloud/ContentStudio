"""
Trend Service — RAG data layer for Viral Intel.

9 free sources scraped in parallel:
  • Reddit       (PRAW)              — top posts matching keyword
  • YouTube      (Data API v3)       — trending videos by view count
  • Google Trends (pytrends)         — rising/top related queries
  • RSS feeds                        — recent headlines matching keyword
  • Hacker News  (Algolia API)       — top HN stories, no auth needed
  • Mastodon     (mastodon.social)   — open social, dev/AI community
  • Wikipedia    (Pageviews API)     — traffic spikes = viral signal
  • TikTok       (Browserless)       — Google-indexed TikTok videos
  • X/Twitter    (twikit)            — tweets via internal web API (+ Browserless fallback)

Browserless: self-hosted Docker container (stealth Chrome, free):
  docker run -p 3000:3000 --restart always -d browserless/chrome:latest

All sources degrade gracefully — missing keys return empty lists, never crash.
"""

import asyncio
import time
import httpx
import feedparser
from datetime import datetime, timedelta, timezone
from app.utils.logger import logger

# ── Simple in-memory cache for Google Trends (avoids 429 on rapid re-scans) ──
_trends_cache: dict[str, tuple[float, list]] = {}
_TRENDS_TTL = 1800  # 30 minutes

# ── Optional dependencies ───────────────────────────────────────────────

try:
    import praw
    PRAW_OK = True
except ImportError:
    PRAW_OK = False
    logger.warning(
        "[TrendService] praw not installed — Reddit disabled. pip install praw==7.7.1")

try:
    from pytrends.request import TrendReq
    PYTRENDS_OK = True
except ImportError:
    PYTRENDS_OK = False
    logger.warning(
        "[TrendService] pytrends not installed — Google Trends disabled. pip install pytrends==4.9.2")

try:
    from playwright.sync_api import sync_playwright as _playwright_import  # noqa: F401
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False
    logger.warning(
        "[TrendService] playwright not installed — Instagram disabled. "
        "pip install playwright && playwright install chromium")

try:
    import twikit as _twikit_import  # noqa: F401
    TWIKIT_OK = True
except ImportError:
    TWIKIT_OK = False
    logger.warning(
        "[TrendService] twikit not installed — X/Twitter disabled. pip install twikit>=2.2.0")

try:
    from bs4 import BeautifulSoup as _BS4  # noqa: F401
    BS4_OK = True
except ImportError:
    BS4_OK = False
    logger.warning(
        "[TrendService] beautifulsoup4 not installed — Browserless disabled. pip install beautifulsoup4")

# Set True when twikit hits an unrecoverable error.
# Stops repeated login attempts for the lifetime of the process.
_twitter_broken = False

# ── twikit: Chrome TLS patch (Cloudflare bypass) + KEY_BYTE fallback ────
#
# Problem 1 — Cloudflare 403:
#   twikit uses Python's httpx which has a Python TLS fingerprint.
#   Cloudflare detects this and blocks requests to x.com.
#   Fix: replace twikit's httpx transport with curl_cffi's Chrome impersonation
#   so every request looks like it's coming from real Chrome 124.
#
# Problem 2 — KEY_BYTE:
#   Twitter periodically updates their anti-bot JS, breaking twikit's parser.
#   Fix: intercept ClientTransaction.init() and catch KEY_BYTE, continue with
#   safe fallback indices so login + search still proceed.
if TWIKIT_OK:
    # ── Patch 1: Chrome TLS via curl_cffi ───────────────────────────────────
    try:
        import httpx as _httpx
        from httpx_curl_cffi import AsyncCurlTransport as _CurlTransport
        from twikit import Client as _TwikitClient

        _orig_twikit_init = _TwikitClient.__init__

        def _chrome_twikit_init(self, language="en-US", proxy=None, **kwargs):
            _orig_twikit_init(self, language=language, proxy=proxy, **kwargs)
            # Replace the httpx client transport with Chrome-impersonating curl_cffi.
            # Keep using httpx.AsyncClient so cookie management stays
            # unchanged.
            self.http = _httpx.AsyncClient(
                transport=_CurlTransport(impersonate="chrome124"),
            )

        _TwikitClient.__init__ = _chrome_twikit_init
        logger.info(
            "[TrendService] twikit Chrome TLS patch applied (curl_cffi) — Cloudflare bypass active")
    except Exception as _e:
        logger.warning(f"[TrendService] twikit Chrome TLS patch failed: {_e}")

    # ── Patch 2: KEY_BYTE fallback ──────────────────────────────────────────
    try:
        from twikit.x_client_transaction.transaction import ClientTransaction as _CT
        _orig_ct_init = _CT.init

        async def _safe_ct_init(self, session, headers):
            try:
                await _orig_ct_init(self, session, headers)
            except Exception as _e:
                if "key_byte" in str(_e).lower():
                    logger.debug(
                        "[TrendService] twikit KEY_BYTE fallback — using safe defaults")
                    self.DEFAULT_ROW_INDEX = 2
                    self.DEFAULT_KEY_BYTES_INDICES = [42, 45]
                    try:
                        self.key = self.get_key(
                            response=self.home_page_response)
                        self.key_bytes = self.get_key_bytes(key=self.key)
                        self.animation_key = (
                            self.get_animation_key(
                                key_bytes=self.key_bytes,
                                response=self.home_page_response) if len(
                                self.key_bytes) > 45 else "0" * 32)
                    except Exception:
                        self.key = ""
                        self.key_bytes = [0] * 50
                        self.animation_key = "0" * 32
                else:
                    raise

        _CT.init = _safe_ct_init
        logger.debug("[TrendService] twikit KEY_BYTE patch applied")
    except Exception as _e:
        logger.warning(f"[TrendService] twikit KEY_BYTE patch failed: {_e}")

# ── RSS feed list (zero API keys needed) ─────────────────────────────────────
# Mix of: AI-specific feeds (high relevance for most keywords) +
#         general tech/business feeds (broader coverage)

RSS_FEEDS = [
    # ── AI / ML focused ────────────────────────────────────────────────────
    "https://feeds.feedburner.com/venturebeat/SZYF",          # VentureBeat AI
    "https://www.technologyreview.com/feed/",                  # MIT Tech Review
    "https://openai.com/news/rss.xml",                         # OpenAI
    "https://blogs.microsoft.com/ai/feed/",                    # Microsoft AI
    "https://news.google.com/rss/search?q=artificial+intelligence+tools&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AI+tools&hl=en-US&gl=US&ceid=US:en",
    # ── General tech / startup ─────────────────────────────────────────────
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.wired.com/feed/rss",
    "https://feeds.feedburner.com/entrepreneur/latest",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    # ── SaaS / growth / marketing ──────────────────────────────────────────
    "https://saastr.com/feed/",                                # SaaStr
    "https://www.saastr.com/feed/",                            # SaaStr alt
    # HubSpot (growth/marketing)
    "https://blog.hubspot.com/feed/",
    # Sarah Scoop (SaaS news)
    "https://sarahscoop.com/feed/",
    "https://feeds.feedburner.com/SaaS_List",                  # SaaS List
]


class TrendService:

    async def fetch_reddit(
            self,
            keyword: str,
            time_filter: str = "week") -> list:
        if not PRAW_OK:
            return []
        try:
            from app.config import settings
            if not settings.REDDIT_CLIENT_ID or not settings.REDDIT_CLIENT_SECRET:
                logger.debug("[TrendService] Reddit keys absent — skipping")
                return []

            loop = asyncio.get_event_loop()

            def _sync():
                r = praw.Reddit(
                    client_id=settings.REDDIT_CLIENT_ID,
                    client_secret=settings.REDDIT_CLIENT_SECRET,
                    user_agent=settings.REDDIT_USER_AGENT or "ContentStudio/1.0",
                    check_for_async=False,
                )
                posts = []
                for post in r.subreddit("all").search(
                    keyword, sort="top", time_filter=time_filter, limit=15
                ):
                    posts.append({
                        "title": post.title,
                        "score": post.score,
                        "comments": post.num_comments,
                        "subreddit": str(post.subreddit),
                        "url": f"https://reddit.com{post.permalink}",
                    })
                return posts

            return await asyncio.wait_for(loop.run_in_executor(None, _sync), timeout=18)
        except Exception as e:
            logger.warning(f"[TrendService] Reddit error: {e}")
            return []

    async def fetch_youtube(self, keyword: str, days: int = 7) -> list:
        try:
            from app.config import settings
            if not settings.YOUTUBE_DATA_API_KEY:
                logger.debug("[TrendService] YouTube key absent — skipping")
                return []

            published_after = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            async with httpx.AsyncClient(timeout=12) as client:
                # YouTube Data API v3 — max 50 per request, costs 100 units per call
                # Free quota: 10,000 units/day → 100 calls/day at maxResults=50
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet", "q": keyword, "type": "video",
                        "order": "viewCount", "publishedAfter": published_after,
                        "maxResults": 50, "key": settings.YOUTUBE_DATA_API_KEY,
                    },
                )
                data = resp.json()

                # Surface API-level errors (quota exceeded, bad key, etc.)
                if "error" in data:
                    err = data["error"]
                    logger.warning(
                        f"[TrendService] YouTube API error {err.get('code')}: "
                        f"{err.get('message', 'unknown')} — "
                        f"{'quota exceeded — resets midnight Pacific' if err.get('code') == 403 else 'check YOUTUBE_DATA_API_KEY'}")
                    return []

                videos = []
                for item in data.get("items", []):
                    s = item.get("snippet", {})
                    videos.append({
                        "title": s.get("title", ""),
                        "channel": s.get("channelTitle", ""),
                        "description": s.get("description", "")[:150],
                        "published": s.get("publishedAt", ""),
                    })

                logger.info(
                    f"[TrendService] YouTube: {len(videos)} videos for '{keyword}' "
                    f"(last {days}d)" + (
                        " — 0 results, try a broader keyword" if not videos else ""))
                return videos
        except Exception as e:
            logger.warning(f"[TrendService] YouTube error: {e}")
            return []

    async def fetch_google_trends(self, keyword: str, days: int = 7) -> list:
        """
        Primary: pytrends → related/rising search queries from Google Trends.
        Fallback: Google News RSS keyword search — never rate-limited, no key needed.

        pytrends is unreliable in production (Google 429s by IP). The fallback
        ensures this source always returns data even when pytrends is blocked.
        Results are cached 30 min to avoid hammering either source.
        """
        # Map days to valid pytrends timeframe strings
        # Google only accepts: now 1-d, now 7-d, today 1-m, today 3-m, today
        # 12-m
        if days <= 1:
            timeframe = "now 1-d"
            tbs = "qdr:d"
        elif days <= 7:
            timeframe = "now 7-d"
            tbs = "qdr:w"
        else:
            timeframe = "today 1-m"
            tbs = "qdr:m"

        cache_key = f"{keyword.lower().strip()}:{timeframe}"
        now = time.time()
        if cache_key in _trends_cache:
            ts, data = _trends_cache[cache_key]
            if now - ts < _TRENDS_TTL:
                logger.debug(
                    f"[TrendService] Google Trends cache hit for '{keyword}'")
                return data

        # ── Primary: pytrends ────────────────────────────────────────────────
        if PYTRENDS_OK:
            try:
                loop = asyncio.get_event_loop()

                def _sync():
                    pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
                    pt.build_payload([keyword], timeframe=timeframe)
                    related = pt.related_queries()
                    kw_data = related.get(keyword, {})
                    for key in ("rising", "top"):
                        df = kw_data.get(key)
                        if df is not None and not df.empty:
                            return df["query"].head(12).tolist()
                    return []

                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _sync), timeout=20
                )
                if result:
                    # Filter out unrelated queries — pytrends "related" queries are
                    # TIME-correlated, not topic-correlated. "non-toxic air fryer" trends
                    # at the same time as "ai tools" but has nothing to do with AI.
                    # Keep only queries that share at least one word with the
                    # keyword.
                    kw_words = set(keyword.lower().split())

                    def _is_on_topic(q: str) -> bool:
                        q_words = set(q.lower().split())
                        # word-level intersection
                        return bool(kw_words & q_words)

                    filtered = [q for q in result if _is_on_topic(q)]
                    # Fall back to top 6 unfiltered if filter removes
                    # everything
                    result = filtered if filtered else result[:6]

                    _trends_cache[cache_key] = (now, result)
                    logger.info(
                        "[TrendService] Google Trends (pytrends) OK — "
                        f"{len(result)} queries (filtered from raw results)"
                    )
                    return result
            except Exception as e:
                logger.warning(
                    f"[TrendService] pytrends failed ({e}) — falling back to Google News RSS")

        # ── Fallback: Google News RSS keyword search ─────────────────────────
        # Reliable, no API key, timeframe-filtered via tbs=qdr: parameter.
        # Returns trending news headlines for the keyword instead of query
        # strings.
        try:
            q = keyword.strip().replace(" ", "+")
            url = (
                "https://news.google.com/rss/search"
                f"?q={q}&hl=en-US&gl=US&ceid=US:en&tbs={tbs}"
            )
            loop = asyncio.get_event_loop()
            feed = await asyncio.wait_for(
                loop.run_in_executor(None, feedparser.parse, url), timeout=10
            )
            topics = []
            for entry in feed.entries[:12]:
                # Strip publisher suffix "- Publisher Name" that Google News
                # appends
                title = entry.get("title", "").rsplit(" - ", 1)[0].strip()
                if title:
                    topics.append(title)

            if topics:
                _trends_cache[cache_key] = (now, topics)
                logger.info(
                    f"[TrendService] Google News RSS fallback OK — {len(topics)} headlines")
            return topics

        except Exception as e2:
            logger.warning(
                f"[TrendService] Google News RSS fallback failed: {e2}")
            return []

    async def fetch_rss(self, keyword: str, days: int = 7) -> list:
        kw_lower = keyword.lower()
        kw_words = [w for w in kw_lower.split() if len(w) >= 2]
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        results = []

        def _text_matches(text: str) -> bool:
            """Match keyword phrase or any keyword word in the given text."""
            t = text.lower()
            if kw_lower in t:
                return True
            return any(w in t for w in kw_words)

        def _in_window(entry) -> bool:
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub:
                try:
                    from time import mktime
                    pub_dt = datetime.fromtimestamp(
                        mktime(pub), tz=timezone.utc)
                    return pub_dt >= cutoff
                except Exception:
                    pass
            return True

        async def _parse(url: str) -> list:
            try:
                loop = asyncio.get_event_loop()
                feed = await asyncio.wait_for(
                    loop.run_in_executor(None, feedparser.parse, url), timeout=8
                )
                title_matches = []
                summary_matches = []
                for entry in feed.entries[:50]:
                    if not _in_window(entry):
                        continue
                    title = entry.get("title", "")
                    if _text_matches(title):
                        title_matches.append({
                            "title": title,
                            "source": feed.feed.get("title", url),
                            "url": entry.get("link", ""),
                        })
                    else:
                        summary = entry.get(
                            "summary", "") or entry.get(
                            "description", "") or ""
                        if _text_matches(summary):
                            summary_matches.append({
                                "title": title,
                                "source": feed.feed.get("title", url),
                                "url": entry.get("link", ""),
                            })
                # Prefer title matches, fall back to summary matches if title
                # matching is sparse
                return title_matches if len(title_matches) >= 2 else (
                    title_matches + summary_matches)[:15]
            except Exception:
                return []

        per_feed = await asyncio.gather(*[_parse(u) for u in RSS_FEEDS], return_exceptions=True)
        for batch in per_feed:
            if isinstance(batch, list):
                results.extend(batch)
        return results[:15]

    async def fetch_hackernews(self, keyword: str, days: int = 7) -> list:
        """
        Hacker News via Algolia API — 100% free, no auth, no rate limits.
        Best for tech/AI/startup/SaaS topics.
        Returns top posts sorted by engagement (points + comments).
        """
        try:
            import time as _time
            time_threshold = int(_time.time()) - (days * 86400)

            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    "https://hn.algolia.com/api/v1/search",
                    params={
                        "query": keyword,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{time_threshold},points>5",
                        "hitsPerPage": 20,
                    },
                )
                resp.raise_for_status()
                hits = resp.json().get("hits", [])

            results = []
            for h in hits:
                points = h.get("points", 0) or 0
                comments = h.get("num_comments", 0) or 0
                results.append({
                    "title": h.get("title", ""),
                    "url": h.get("url", ""),
                    "points": points,
                    "comments": comments,
                    "score": points + (comments * 2),
                    "source": "HackerNews",
                })

            results.sort(key=lambda x: x["score"], reverse=True)
            logger.info(
                f"[TrendService] HackerNews: {len(results)} stories for '{keyword}'")
            return results[:15]

        except Exception as e:
            logger.warning(f"[TrendService] HackerNews error: {e}")
            return []

    async def fetch_mastodon(self, keyword: str, days: int = 7) -> list:
        """
        Mastodon trend data via public hashtag timelines — zero auth, zero rate limits.

        Why hashtag timeline instead of search API:
          /api/v2/search?type=statuses requires authentication for full-text status search.
          /api/v1/timelines/tag/{hashtag} is fully public — no token needed.

        Strategy: try up to 3 hashtag variations derived from the keyword.
        e.g. 'AI agents' → tries #aiagents, #ai, #agents
        Returns posts from the variation that yields the most results.
        """
        import re as _re

        def _to_hashtag(text: str) -> str:
            """Convert any keyword phrase to a clean hashtag slug."""
            return _re.sub(r"[^a-z0-9]", "", text.lower().replace(" ", ""))

        # Build up to 3 candidate hashtags from the keyword
        words = keyword.strip().split()
        slug = _to_hashtag(keyword)                              # 'aiagents'
        first = _to_hashtag(words[0]) if words else slug          # 'ai'
        second = _to_hashtag(words[1]) if len(words) > 1 else None  # 'agents'

        # Short slug expansions (e.g. 'ai' has its own popular hashtag on
        # Mastodon)
        expansions = {
            "ai": "artificialintelligence", "ml": "machinelearning",
            "vr": "virtualreality", "ar": "augmentedreality",
            "llm": "llm", "ux": "ux",
        }
        candidates = [slug]
        if slug in expansions:
            candidates.insert(0, expansions[slug])
        if first and first != slug:
            candidates.append(first)
        if second and second not in candidates:
            candidates.append(second)

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            best: list = []
            used_tag = slug

            async with httpx.AsyncClient(timeout=12) as client:
                for tag in candidates[:3]:
                    resp = await client.get(
                        f"https://mastodon.social/api/v1/timelines/tag/{tag}",
                        params={"limit": 40},
                    )
                    if resp.status_code != 200:
                        continue
                    statuses = resp.json()
                    if len(statuses) > len(best):
                        best = statuses
                        used_tag = tag
                    if len(best) >= 20:
                        break  # good enough, stop trying

            results = []
            for s in best:
                try:
                    created = datetime.fromisoformat(
                        s["created_at"].replace("Z", "+00:00"))
                    if created < cutoff:
                        continue
                    text = _re.sub(
                        r"<[^>]+>",
                        " ",
                        s.get(
                            "content",
                            "")).strip()
                    if not text:
                        continue
                    results.append({
                        "text": text[:280],
                        "replies": s.get("replies_count", 0),
                        "shares": s.get("reblogs_count", 0),
                        "likes": s.get("favourites_count", 0),
                        "source": "Mastodon",
                    })
                except Exception:
                    pass

            logger.info(
                f"[TrendService] Mastodon: {len(results)} posts for "
                f"'#{used_tag}' (keyword: '{keyword}')"
            )
            return results[:15]

        except Exception as e:
            logger.warning(f"[TrendService] Mastodon error: {e}")
            return []

    async def fetch_wikipedia_trends(
            self, keyword: str, days: int = 7) -> list:
        """
        Wikipedia Pageviews API — completely open, no key, no auth.
        Traffic spikes = viral signal. When a topic blows up on social media,
        people search Wikipedia immediately. Tracks that acceleration.
        """
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
            start_str = start.strftime("%Y%m%d")
            end_str = end.strftime("%Y%m%d")

            # Normalise keyword → Wikipedia article title format
            article = keyword.strip().replace(" ", "_")

            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
                    f"/en.wikipedia/all-access/all-agents/{article}/daily/{start_str}/{end_str}",
                    headers={"User-Agent": "ContentStudio/1.0 (trend-scout)"},
                )
                if resp.status_code == 404:
                    return []   # article doesn't exist — silent skip
                resp.raise_for_status()
                items = resp.json().get("items", [])

            if not items:
                return []

            total_views = sum(i.get("views", 0) for i in items)
            # Detect spike: last 3 days vs earlier days
            recent = sum(i.get("views", 0) for i in items[-3:])
            earlier = sum(i.get("views", 0) for i in items[:-3]) or 1
            spike = round(recent / (earlier / max(len(items) - 3, 1)), 2)

            result = [{
                "article": article.replace("_", " "),
                "total_views": total_views,
                "daily_avg": round(total_views / len(items)),
                "spike_ratio": spike,   # >1 means accelerating interest
                "trending": spike > 1.5,
                "source": "Wikipedia",
            }]
            logger.info(
                f"[TrendService] Wikipedia '{article}': {total_views:,} views, "
                f"spike={spike}x over {days}d")
            return result

        except Exception as e:
            logger.warning(f"[TrendService] Wikipedia error: {e}")
            return []

    async def fetch_via_browserless(
        self,
        keyword: str,
        sites: list,
        days: int = 7,
        max_results: int = 15,
    ) -> list:
        """
        Generic stealth scraper — queries Google Search filtered to specific social sites,
        rendered via a local self-hosted Browserless Docker container (free, no API key).

        Architecture:
          [Python] → POST http://localhost:3000/content → [Browserless Chrome] → [Google Search]
                                                          (real Chrome fingerprint, JS rendered)

        Why Google as the intermediary:
        • Google has already indexed all public Instagram, Twitter, TikTok content
        • Google's tbs=qdr: date filter bounds results to the exact requested timeframe
        • We never hit platform servers directly — no logins, no bans, no Cloudflare
        • Browserless patches navigator.webdriver + browser headers → passes bot checks

        Returns silently empty list if the Docker container is not running.
        Run it once:
          docker run -p 3000:3000 --restart always -d browserless/chrome:latest
        """
        if not BS4_OK:
            return []
        try:
            from app.config import settings
            from bs4 import BeautifulSoup
            import urllib.parse

            browserless_url = getattr(
                settings, "BROWSERLESS_URL", "http://localhost:3000")

            # Map days → Google's relative date code (qdr = query date range)
            if days <= 1:
                tbs = "qdr:d"
            elif days <= 7:
                tbs = "qdr:w"
            else:
                tbs = "qdr:m"

            site_filter = " OR ".join(f"site:{s}" for s in sites)
            query = urllib.parse.quote(f"{keyword} {site_filter}")
            target_url = (
                "https://www.google.com/search"
                f"?q={query}&tbs={tbs}&num=20&hl=en&gl=us"
            )

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{browserless_url}/content",
                    json={
                        "url": target_url,
                        # Drop images/fonts/styles — we only need the text HTML
                        "rejectResourceTypes": ["image", "font", "stylesheet"],
                    },
                    headers={"Content-Type": "application/json"},
                )

            if resp.status_code != 200:
                logger.debug(
                    f"[Browserless] HTTP {resp.status_code} — container not running?")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            # Google wraps each organic result in <div class="g">.
            # Fallback to data-sokoban-container if Google changed the class
            # name.
            containers = soup.find_all("div", class_="g")
            if not containers:
                containers = soup.find_all(
                    "div", attrs={"data-sokoban-container": True})

            for g in containers:
                title_el = g.find("h3")
                if not title_el:
                    continue

                # Google uses multiple snippet class names across versions —
                # try all known ones
                snippet_el = (
                    g.find("div", class_="VwiC3b") or
                    g.find("div", class_="s") or
                    g.find("span", class_="aCOpRe")
                )
                link_el = g.find("a")

                title = title_el.get_text(strip=True)
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                url = link_el.get("hre", "") if link_el else ""

                # Skip Google's own navigation links
                if not url or (
                        url.startswith("https://www.google.com") and "/search" in url):
                    continue

                # Identify which social site this result is from
                source = "Web"
                for s in sites:
                    if s in url:
                        source = s.split(".")[0].capitalize()
                        break

                results.append({
                    "title": title,
                    "snippet": snippet[:300],
                    "url": url,
                    "source": source,
                })
                if len(results) >= max_results:
                    break

            logger.info(
                f"[Browserless] {len(results)} results for '{keyword}' "
                f"via {[s.split('.')[0] for s in sites]}"
            )
            return results

        except httpx.ConnectError:
            # Container not running — silent skip, other sources still work
            logger.debug(
                "[Browserless] Container unreachable. "
                "Start it: docker run -p 3000:3000 -d browserless/chrome:latest")
            return []
        except Exception as e:
            logger.debug(f"[Browserless] Unexpected error: {e}")
            return []

    async def fetch_tiktok(self, keyword: str, days: int = 7) -> list:
        """
        TikTok trend data via Google-indexed public TikTok videos + Browserless.

        TikTok has no public API and actively blocks scrapers.
        Using Google as an intermediary solves both problems:
        Google indexes all public TikTok video titles and descriptions,
        and Browserless passes Google's bot checks with real Chrome headers.

        Returns silently empty if Browserless container is not running.
        """
        results = await self.fetch_via_browserless(keyword, ["tiktok.com"], days)
        if results:
            logger.info(
                f"[TrendService] TikTok (via Browserless): {len(results)} results for '{keyword}'")
        return results

    async def fetch_twitter(self, keyword: str, days: int = 7) -> list:
        """
        Search recent X/Twitter posts for viral content signals via twikit.

        Human-behaviour hardening (bot detection avoidance):
          • Cookie reuse — login called ONCE, session persists via JSON cookies.
          • Random pre-search delay (2-6 s) — simulates real user behaviour.
          • Random per-result micro-delay (100-400 ms) — avoids burst-read pattern.
          • Auth-error cookie clearing — stale cookies wiped so next call re-logins.
          • KEY_BYTE graceful handling — if twikit's JS parser breaks (Twitter changed
            their anti-bot code), disables Twitter for the process lifetime instead of
            spamming failed logins on every scan.
        """
        global _twitter_broken
        if not TWIKIT_OK or _twitter_broken:
            return []
        try:
            import random
            import os
            from app.config import settings
            if not settings.TWITTER_SCRAPER_USERNAME or not settings.TWITTER_SCRAPER_PASSWORD:
                logger.debug(
                    "[TrendService] Twitter scraper credentials absent — skipping")
                return []

            from twikit import Client

            cookies_path = "twitter_scraper_cookies.json"
            client = Client("en-US")

            if os.path.exists(cookies_path):
                client.load_cookies(cookies_path)
                logger.debug(
                    "[TrendService] Twitter: loaded saved session cookies")
            else:
                logger.info(
                    "[TrendService] Twitter: no cookies found — logging in (first run only)")
                await client.login(
                    auth_info_1=settings.TWITTER_SCRAPER_USERNAME,
                    auth_info_2=settings.TWITTER_SCRAPER_EMAIL or settings.TWITTER_SCRAPER_USERNAME,
                    password=settings.TWITTER_SCRAPER_PASSWORD,
                )
                client.save_cookies(cookies_path)
                logger.info(
                    "[TrendService] Twitter: session cookies saved — future calls skip login")

            # Human delay — real users don't search the millisecond their
            # session is ready
            await asyncio.sleep(random.uniform(2.0, 6.0))

            tweets = await asyncio.wait_for(
                client.search_tweet(keyword, "Latest"),
                timeout=18,
            )

            results = []
            for t in tweets:
                try:
                    results.append({
                        "text": t.text[:280],
                        "likes": t.favorite_count or 0,
                        "retweets": t.retweet_count or 0,
                        "replies": t.reply_count or 0,
                        "author": getattr(t.user, "name", ""),
                    })
                    await asyncio.sleep(random.uniform(0.1, 0.4))
                except Exception:
                    pass
                if len(results) >= 15:
                    break

            logger.info(
                f"[TrendService] Twitter: {len(results)} tweets for '{keyword}'")
            return results

        except Exception as e:
            err_str = str(e).lower()

            # KEY_BYTE = twikit can't parse Twitter's anti-bot JS — library bug, not credentials.
            # Disable for process lifetime to stop spamming failed logins on
            # every scan.
            if "key_byte" in err_str:
                _twitter_broken = True
                logger.warning(
                    "[TrendService] Twitter disabled: twikit KEY_BYTE error — "
                    "Twitter changed their anti-bot JS, twikit parser broken. "
                    "Other sources unaffected. Will auto-recover when twikit releases a fix.")
                return []

            # Rate limit
            if any(
                w in err_str for w in (
                    "rate_limit",
                    "rate limit",
                    "429",
                    "too many")):
                logger.warning(
                    "[TrendService] Twitter rate-limited — backing of")
                return []

            # Cloudflare block — x.com blocked the raw HTTP request (bot detection)
            # twikit needs to fetch x.com to complete login; Cloudflare blocks
            # headless clients.
            if "cloudflare" in err_str or (
                    "403" in err_str and "x.com" in err_str):
                _twitter_broken = True
                logger.warning(
                    "[TrendService] Twitter disabled: Cloudflare blocked the request to x.com. "
                    "twikit makes raw HTTP calls that Cloudflare flags as bots. "
                    "Other sources unaffected.")
                return []

            # Auth errors — clear stale cookies so next call re-logins cleanly
            try:
                import os
                if os.path.exists("twitter_scraper_cookies.json"):
                    if any(
                        w in err_str for w in (
                            "auth",
                            "login",
                            "unauthorized",
                            "forbidden")):
                        os.remove("twitter_scraper_cookies.json")
                        logger.info(
                            "[TrendService] Twitter: stale cookies cleared — will re-login next call")
            except Exception:
                pass

            logger.warning(f"[TrendService] Twitter error: {e}")
            return []

    async def aggregate(self, keyword: str, days: int = 7) -> dict:
        """
        Run all 10 sources in parallel. Each fails independently.

        Sources:
          Always active (no keys):  RSS, Google Trends, Hacker News, Mastodon, Wikipedia
          Keys needed:              Reddit (REDDIT_CLIENT_ID), YouTube (YOUTUBE_DATA_API_KEY)
          Account needed:           Twitter (twikit)
          Docker needed:            TikTok (Browserless → Google Search)

        Fallback for Twitter:
          twikit fails → Browserless Google Search fallback (if Docker running)
        """
        time_filter = "week" if days <= 7 else "month"

        # ── Run all 8 sources in parallel (Twitter/X removed — twikit + Cloudflare unreliable) ──
        (
            reddit, youtube, trends, rss,
            hackernews, mastodon, wikipedia,
            tiktok,
        ) = await asyncio.gather(
            self.fetch_reddit(keyword, time_filter),
            self.fetch_youtube(keyword, days),
            self.fetch_google_trends(keyword, days),
            self.fetch_rss(keyword, days),
            self.fetch_hackernews(keyword, days),
            self.fetch_mastodon(keyword, days),
            self.fetch_wikipedia_trends(keyword, days),
            self.fetch_tiktok(keyword, days),
            return_exceptions=True,
        )

        def safe(v) -> list:
            return v if isinstance(v, list) else []

        reddit = safe(reddit)
        youtube = safe(youtube)
        trends = safe(trends)
        rss = safe(rss)
        hackernews = safe(hackernews)
        mastodon = safe(mastodon)
        wikipedia = safe(wikipedia)
        tiktok = safe(tiktok)

        return {
            "keyword": keyword,
            "days": days,
            "reddit": reddit,
            "youtube": youtube,
            "google_trends": trends,
            "rss": rss,
            "hackernews": hackernews,
            "mastodon": mastodon,
            "wikipedia": wikipedia,
            "tiktok": tiktok,
            "sources_active": {
                "reddit": len(reddit) > 0,
                "youtube": len(youtube) > 0,
                "google_trends": len(trends) > 0,
                "rss": len(rss) > 0,
                "hackernews": len(hackernews) > 0,
                "mastodon": len(mastodon) > 0,
                "wikipedia": len(wikipedia) > 0,
                "tiktok": len(tiktok) > 0,
            },
        }


trend_service = TrendService()
