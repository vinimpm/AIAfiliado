"""
Trend Engine Service
====================
Collects trending topics from Google Trends and TikTok Creative Center,
scores them using a multi-criteria algorithm, filters and deduplicates,
then persists qualified trends to the database.
"""

from __future__ import annotations

import random
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup
from pytrends.request import TrendReq
from sqlalchemy import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from app.celery_app import celery
from app.config import settings
from app.logging import get_logger
from models.database import get_session_cm
from models.trend import Trend

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = get_logger(service="trend_engine")

# ---------------------------------------------------------------------------
# Module-level cache for Google Trends results (simple TTL cache)
# ---------------------------------------------------------------------------
_google_cache: dict[str, Any] = {
    "data": None,
    "expires_at": None,
}

_GOOGLE_CACHE_TTL_HOURS = 6

# ---------------------------------------------------------------------------
# User-Agent rotation list for TikTok CC scraping
# ---------------------------------------------------------------------------
_USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
]

# ---------------------------------------------------------------------------
# Blocked categories -- trends in these categories are automatically discarded
# ---------------------------------------------------------------------------
_BLOCKED_CATEGORIES: set[str] = {
    "politics",
    "religion",
    "gambling",
    "adult",
    "weapons",
    "drugs",
    "tobacco",
    "alcohol",
}

# ---------------------------------------------------------------------------
# Patterns used for the "repetibilidade" heuristic
# ---------------------------------------------------------------------------
_PATTERN_SCORES: list[tuple[re.Pattern, int]] = [
    (re.compile(r"(challenge|desafio)", re.IGNORECASE), 80),
    (re.compile(r"(review|resenha|unboxing)", re.IGNORECASE), 85),
    (re.compile(r"(tutorial|como\s+fazer|how\s+to|diy)", re.IGNORECASE), 90),
    (re.compile(r"(haul|compras|comprinhas)", re.IGNORECASE), 85),
    (re.compile(r"(rotina|routine|morning|night)", re.IGNORECASE), 75),
    (re.compile(r"(receita|recipe)", re.IGNORECASE), 70),
]


# ===========================================================================
#  Google Trends collection
# ===========================================================================
@retry(
    retry=retry_if_exception_type((requests.exceptions.RequestException, Exception)),
    stop=stop_after_attempt(3),
    wait=wait_fixed(30),
    reraise=True,
)
def _fetch_google_trends_raw() -> list[dict]:
    """Low-level fetcher wrapped with tenacity retry for rate-limit handling."""
    pytrends = TrendReq(hl="pt-BR", tz=180, retries=2, backoff_factor=0.5)

    trending_df = pytrends.trending_searches(pn="brazil")
    # trending_searches returns a DataFrame with column 0
    keywords: list[str] = trending_df[0].tolist()[:20]

    results: list[dict] = []
    # Process in batches of 5 (pytrends limit)
    for i in range(0, len(keywords), 5):
        batch = keywords[i : i + 5]
        try:
            pytrends.build_payload(batch, timeframe="now 7-d", geo="BR")
            iot_df = pytrends.interest_over_time()

            for kw in batch:
                growth_rate = 0.0
                if not iot_df.empty and kw in iot_df.columns:
                    series = iot_df[kw]
                    if len(series) >= 2:
                        first_half = series[: len(series) // 2].mean()
                        second_half = series[len(series) // 2 :].mean()
                        if first_half > 0:
                            growth_rate = round(((second_half - first_half) / first_half) * 100, 2)
                        elif second_half > 0:
                            growth_rate = 100.0

                results.append(
                    {
                        "name": kw.strip(),
                        "source": "google_trends",
                        "growth_rate": growth_rate,
                        "category": None,
                        "views": None,
                        "growth_7d": growth_rate,
                        "video_count": None,
                    }
                )

            # Respectful delay between batches
            time.sleep(random.uniform(2, 4))

        except Exception as exc:
            logger.warning(
                "google_trends_batch_error",
                batch=batch,
                error=str(exc),
            )
            # Still add keywords with zero growth so they can be scored
            for kw in batch:
                results.append(
                    {
                        "name": kw.strip(),
                        "source": "google_trends",
                        "growth_rate": 0.0,
                        "category": None,
                        "views": None,
                        "growth_7d": 0.0,
                        "video_count": None,
                    }
                )

    return results


def _collect_google_trends() -> list[dict]:
    """
    Collect trending topics from Google Trends (Brazil).
    Results are cached in a module-level dict with a 6-hour TTL.
    """
    global _google_cache

    now = datetime.now(UTC)

    # Return cached data if still valid
    if (
        _google_cache["data"] is not None
        and _google_cache["expires_at"] is not None
        and now < _google_cache["expires_at"]
    ):
        logger.info("google_trends_cache_hit", cached_count=len(_google_cache["data"]))
        return _google_cache["data"]

    logger.info("google_trends_collecting")

    try:
        results = _fetch_google_trends_raw()
        _google_cache["data"] = results
        _google_cache["expires_at"] = now + timedelta(hours=_GOOGLE_CACHE_TTL_HOURS)
        logger.info("google_trends_collected", count=len(results))
        return results

    except Exception as exc:
        logger.error("google_trends_failed", error=str(exc), exc_info=True)
        # Return stale cache if available, otherwise empty
        if _google_cache["data"] is not None:
            logger.warning("google_trends_returning_stale_cache")
            return _google_cache["data"]
        return []


# ===========================================================================
#  TikTok Creative Center collection
# ===========================================================================
def _collect_tiktok_cc() -> list[dict]:
    """
    Scrape TikTok Creative Center popular hashtags page for Brazil.
    Returns an empty list if scraping fails (fail gracefully).
    """
    url = "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/"
    params = {
        "period": "7",
        "countryCode": "BR",
    }

    ua = random.choice(_USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://ads.tiktok.com/",
    }

    logger.info("tiktok_cc_collecting", url=url, user_agent=ua[:40])

    try:
        # Respectful initial delay
        time.sleep(random.uniform(5, 10))

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        results: list[dict] = []

        # TikTok Creative Center renders hashtag cards in various container
        # structures.  We try multiple selectors to be resilient to layout
        # changes.
        card_selectors = [
            "div[class*='CardPc']",
            "div[class*='hashtag-card']",
            "div[class*='RankingCard']",
            "tr[class*='RankItem']",
            "div[class*='trendingCard']",
        ]

        cards = []
        for selector in card_selectors:
            cards = soup.select(selector)
            if cards:
                break

        if not cards:
            # Fallback: look for any structured list of hashtag-like items
            cards = soup.select("a[href*='hashtag']")

        logger.info("tiktok_cc_cards_found", count=len(cards))

        for card in cards[:30]:
            try:
                raw = _parse_tiktok_card(card)
                if raw and raw.get("name"):
                    results.append(raw)
            except Exception as parse_exc:
                logger.debug(
                    "tiktok_cc_card_parse_error",
                    error=str(parse_exc),
                )
                continue

        # If we got no structured cards, try to extract hashtag names from
        # the full page text as a last resort
        if not results:
            results = _extract_tiktok_fallback(soup)

        logger.info("tiktok_cc_collected", count=len(results))
        return results

    except requests.exceptions.RequestException as exc:
        logger.warning("tiktok_cc_request_failed", error=str(exc))
        return []
    except Exception as exc:
        logger.error("tiktok_cc_failed", error=str(exc), exc_info=True)
        return []


def _parse_tiktok_card(card) -> dict | None:
    """Extract trend data from a single TikTok CC card element."""
    name = None
    views = None
    growth_7d = None
    category = None
    video_count = None

    # Try to extract the hashtag name
    name_el = (
        card.select_one("span[class*='title']")
        or card.select_one("span[class*='name']")
        or card.select_one("a[class*='hashtag']")
        or card.select_one("h3")
        or card.select_one("a")
    )
    if name_el:
        name = name_el.get_text(strip=True)
    elif card.get_text(strip=True):
        name = card.get_text(strip=True)

    if not name:
        return None

    # Clean the name: remove leading # if present, strip whitespace
    name = name.lstrip("#").strip()
    if not name:
        return None

    # Try to extract views / video count from sibling or child elements
    stat_elements = card.select("span[class*='count'], span[class*='num'], span[class*='view']")
    for stat_el in stat_elements:
        text = stat_el.get_text(strip=True).lower()
        parsed = _parse_tiktok_number(text)
        if parsed is not None:
            if views is None:
                views = parsed
            elif video_count is None:
                video_count = parsed

    # Try to extract growth percentage
    growth_el = card.select_one(
        "span[class*='change'], span[class*='growth'], span[class*='percent']"
    )
    if growth_el:
        growth_text = growth_el.get_text(strip=True)
        growth_match = re.search(r"([-+]?\d+(?:\.\d+)?)", growth_text)
        if growth_match:
            growth_7d = float(growth_match.group(1))

    # Try to extract category
    cat_el = card.select_one("span[class*='category'], span[class*='industry'], div[class*='tag']")
    if cat_el:
        category = cat_el.get_text(strip=True).lower()

    # TikTok CC trending page implies significant growth even when we
    # can't extract the exact percentage — use 50% as a sensible baseline.
    effective_growth = growth_7d if growth_7d else 50.0

    return {
        "name": name,
        "source": "tiktok_cc",
        "views": views,
        "growth_7d": effective_growth,
        "growth_rate": effective_growth,
        "category": category,
        "video_count": video_count,
    }


def _parse_tiktok_number(text: str) -> int | None:
    """Parse TikTok-style numbers like '1.2M', '500K', '3.4B'."""
    text = text.strip().replace(",", "").replace(" ", "")
    multipliers = {
        "b": 1_000_000_000,
        "bi": 1_000_000_000,
        "m": 1_000_000,
        "mi": 1_000_000,
        "k": 1_000,
        "mil": 1_000,
    }

    for suffix, mult in multipliers.items():
        match = re.match(rf"^([\d.]+)\s*{suffix}$", text, re.IGNORECASE)
        if match:
            try:
                return int(float(match.group(1)) * mult)
            except ValueError:
                continue

    # Plain integer
    plain = re.match(r"^(\d+)$", text)
    if plain:
        return int(plain.group(1))

    return None


def _extract_tiktok_fallback(soup: BeautifulSoup) -> list[dict]:
    """Fallback extraction: find any hashtag-like patterns in the page."""
    results: list[dict] = []
    seen: set[str] = set()

    # Look for #hashtag patterns in all text
    all_text = soup.get_text()
    hashtags = re.findall(r"#(\w{3,50})", all_text)

    for tag in hashtags[:20]:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            results.append(
                {
                    "name": tag,
                    "source": "tiktok_cc",
                    "views": None,
                    "growth_7d": 50.0,
                    "growth_rate": 50.0,
                    "category": None,
                    "video_count": None,
                }
            )

    return results


# ===========================================================================
#  Scoring algorithm
# ===========================================================================
def _calc_trend_score(trend: dict) -> float:
    """
    Multi-criteria scoring algorithm.

    Weights:
      - crescimento   (40%): growth rate mapped to 0-100
      - repetibilidade (20%): heuristic based on name patterns
      - comprabilidade (20%): category in ALLOWED_CATEGORIES
      - saturacao_inv  (20%): inverse saturation from video_count

    Returns a float between 0 and 100.
    """
    crescimento = _score_crescimento(trend.get("growth_rate") or 0.0)
    repetibilidade = _score_repetibilidade(trend.get("name", ""))
    comprabilidade = _score_comprabilidade(trend.get("category"))
    saturacao_inv = _score_saturacao_inv(trend.get("video_count"))

    score = (
        crescimento * 0.40 + repetibilidade * 0.20 + comprabilidade * 0.20 + saturacao_inv * 0.20
    )

    return round(min(max(score, 0.0), 100.0), 2)


def _score_crescimento(growth_rate: float) -> float:
    """Map growth_rate percentage to a 0-100 score."""
    if growth_rate <= 0:
        return 0.0
    if growth_rate >= 200:
        return 100.0
    if growth_rate >= 150:
        return 95.0
    if growth_rate >= 100:
        return 85.0
    if growth_rate >= 75:
        return 75.0
    if growth_rate >= 50:
        return 65.0
    if growth_rate >= 30:
        return 50.0
    if growth_rate >= 15:
        return 35.0
    if growth_rate >= 5:
        return 20.0
    return 10.0


def _score_repetibilidade(name: str) -> float:
    """
    Heuristic based on trend name patterns.
    Hashtag challenges, product reviews, tutorials etc. score higher
    because they map well to repeatable video formats.
    """
    for pattern, score in _PATTERN_SCORES:
        if pattern.search(name):
            return float(score)
    return 50.0


def _score_comprabilidade(category: str | None) -> float:
    """
    Check if the trend's category is among the allowed (buyable) categories.
    Returns 100 if the category is allowed, 30 if unknown (None),
    and 10 if category is known but not in the allowed list.
    """
    if category is None:
        # Unknown category -- moderate score, benefit of the doubt
        return 30.0

    category_lower = category.lower().strip()
    allowed = {c.lower() for c in settings.ALLOWED_CATEGORIES}

    if category_lower in allowed:
        return 100.0

    # Partial / fuzzy match -- if category contains an allowed keyword
    for allowed_cat in allowed:
        if allowed_cat in category_lower or category_lower in allowed_cat:
            return 80.0

    return 10.0


def _score_saturacao_inv(video_count: int | None) -> float:
    """
    Inverse saturation score based on video_count thresholds.
    Fewer videos = less saturated = higher score.
    """
    if video_count is None:
        # Unknown -- assume moderate saturation
        return 50.0

    if video_count < 1_000:
        return 100.0
    if video_count < 10_000:
        return 80.0
    if video_count < 50_000:
        return 60.0
    if video_count < 200_000:
        return 40.0
    if video_count < 1_000_000:
        return 20.0
    return 0.0


# ===========================================================================
#  Helpers
# ===========================================================================
def _estimate_window_days(growth_rate: float) -> int:
    """
    Estimate how many days the trend window is viable.
    Fast growth = shorter window (capitalize quickly).
    Slow growth = longer window.
    """
    if growth_rate >= 100:
        return 3  # fast -- exploit immediately
    if growth_rate >= 30:
        return 5  # moderate
    return 7  # slow or unknown


def _is_blocked_category(category: str | None) -> bool:
    """Check if a category is in the blocked list."""
    if category is None:
        return False
    return category.lower().strip() in _BLOCKED_CATEGORIES


def _get_existing_trend_names_this_week(session) -> set[str]:
    """
    Query the database for trend names already processed this week
    to avoid duplicates.
    """
    today = datetime.now(UTC).date()
    # ISO week starts on Monday
    week_start = today - timedelta(days=today.weekday())
    week_start_dt = datetime(week_start.year, week_start.month, week_start.day, tzinfo=UTC)

    stmt = (
        select(Trend.name)
        .where(Trend.created_at >= week_start_dt)
        .where(Trend.status.in_(["active", "expired"]))
    )
    rows = session.execute(stmt).scalars().all()
    return {name.lower().strip() for name in rows}


def _normalize_trend_name(name: str) -> str:
    """Normalize trend name for deduplication comparison."""
    return re.sub(r"[^a-z0-9\s]", "", name.lower()).strip()


# ===========================================================================
#  Main Celery task
# ===========================================================================
@celery.task(
    name="services.trend_engine.collect_trends",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def collect_trends(self, run_id: int) -> list[dict]:
    """
    Main Celery task: collect trends from all sources, score, filter,
    deduplicate, and persist to the database.

    Parameters
    ----------
    run_id : int
        The DailyRun ID this collection belongs to.

    Returns
    -------
    list[dict]
        List of qualified trend dicts that were saved to the database.
    """
    logger.info("trend_engine_start", run_id=run_id)

    # ------------------------------------------------------------------
    # Step 1: Collect raw trends from all sources
    # ------------------------------------------------------------------
    raw_trends: list[dict] = []

    # Google Trends
    try:
        google_trends = _collect_google_trends()
        raw_trends.extend(google_trends)
        logger.info(
            "trend_engine_google_done",
            run_id=run_id,
            count=len(google_trends),
        )
    except Exception as exc:
        logger.error(
            "trend_engine_google_error",
            run_id=run_id,
            error=str(exc),
            exc_info=True,
        )

    # TikTok Creative Center
    try:
        tiktok_trends = _collect_tiktok_cc()
        raw_trends.extend(tiktok_trends)
        logger.info(
            "trend_engine_tiktok_done",
            run_id=run_id,
            count=len(tiktok_trends),
        )
    except Exception as exc:
        logger.error(
            "trend_engine_tiktok_error",
            run_id=run_id,
            error=str(exc),
            exc_info=True,
        )

    if not raw_trends:
        logger.warning("trend_engine_no_raw_trends", run_id=run_id)
        return []

    logger.info(
        "trend_engine_raw_collected",
        run_id=run_id,
        total_raw=len(raw_trends),
    )

    # ------------------------------------------------------------------
    # Step 2: Score all trends
    # ------------------------------------------------------------------
    scored_trends: list[dict] = []
    for trend in raw_trends:
        score = _calc_trend_score(trend)
        trend["score"] = score
        trend["window_days"] = _estimate_window_days(trend.get("growth_rate") or 0.0)
        scored_trends.append(trend)

    logger.info(
        "trend_engine_scored",
        run_id=run_id,
        total_scored=len(scored_trends),
    )

    # ------------------------------------------------------------------
    # Step 3: Filter by minimum score
    # ------------------------------------------------------------------
    min_score = settings.TREND_MIN_SCORE
    qualified = [t for t in scored_trends if t["score"] >= min_score]
    logger.info(
        "trend_engine_filtered_score",
        run_id=run_id,
        min_score=min_score,
        qualified=len(qualified),
        discarded=len(scored_trends) - len(qualified),
    )

    # ------------------------------------------------------------------
    # Step 4: Discard blocked categories
    # ------------------------------------------------------------------
    before_block = len(qualified)
    qualified = [t for t in qualified if not _is_blocked_category(t.get("category"))]
    blocked_count = before_block - len(qualified)
    if blocked_count > 0:
        logger.info(
            "trend_engine_blocked_categories",
            run_id=run_id,
            blocked=blocked_count,
        )

    # ------------------------------------------------------------------
    # Step 5: Deduplicate (same name already processed this week)
    # ------------------------------------------------------------------
    with get_session_cm() as session:
        existing_names = _get_existing_trend_names_this_week(session)

    seen_in_batch: set[str] = set()
    deduplicated: list[dict] = []
    for trend in qualified:
        normalized = _normalize_trend_name(trend["name"])
        if normalized in existing_names:
            logger.debug(
                "trend_engine_duplicate_db",
                name=trend["name"],
            )
            continue
        if normalized in seen_in_batch:
            logger.debug(
                "trend_engine_duplicate_batch",
                name=trend["name"],
            )
            continue
        seen_in_batch.add(normalized)
        deduplicated.append(trend)

    logger.info(
        "trend_engine_deduplicated",
        run_id=run_id,
        before=len(qualified),
        after=len(deduplicated),
    )

    # ------------------------------------------------------------------
    # Step 6: Sort by score descending
    # ------------------------------------------------------------------
    deduplicated.sort(key=lambda t: t["score"], reverse=True)

    # ------------------------------------------------------------------
    # Step 7: Persist to database
    # ------------------------------------------------------------------
    saved_trends: list[dict] = []

    with get_session_cm() as session:
        for trend_data in deduplicated:
            now_utc = datetime.now(UTC)
            window = trend_data.get("window_days", 5)

            trend_obj = Trend(
                run_id=run_id,
                name=trend_data["name"],
                score=trend_data["score"],
                window_days=window,
                source=trend_data["source"],
                category=trend_data.get("category"),
                platform=_infer_platform(trend_data["source"]),
                status="active",
                expires_at=now_utc + timedelta(days=window),
            )
            session.add(trend_obj)
            session.flush()  # get the generated ID

            saved_dict = {
                "id": trend_obj.id,
                "run_id": run_id,
                "name": trend_obj.name,
                "score": float(trend_obj.score),
                "window_days": trend_obj.window_days,
                "source": trend_obj.source,
                "category": trend_obj.category,
                "platform": trend_obj.platform,
                "status": trend_obj.status,
                "expires_at": trend_obj.expires_at.isoformat() if trend_obj.expires_at else None,
            }
            saved_trends.append(saved_dict)

        # Commit happens automatically via get_session_cm context manager

    logger.info(
        "trend_engine_complete",
        run_id=run_id,
        saved=len(saved_trends),
    )

    return saved_trends


def _infer_platform(source: str) -> str | None:
    """Map source to the primary platform for content creation."""
    mapping = {
        "google_trends": None,
        "tiktok_cc": "tiktok",
        "tiktok_shop": "tiktok",
    }
    return mapping.get(source)
