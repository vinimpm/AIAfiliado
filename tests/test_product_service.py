"""Tests for the product_service module.

Covers product validation logic, commercial score calculation,
and weekly limit checking.
"""

from unittest.mock import MagicMock, patch

from services.product_service import (
    _calc_commercial_score,
    _validate_product,
    check_weekly_limit,
)

# ===================================================================
# _validate_product
# ===================================================================


class TestValidateProduct:
    """Test the _validate_product function against mandatory commercial criteria."""

    def _make_raw(self, **overrides) -> dict:
        """Build a raw product dict with sensible defaults, overridable per test."""
        defaults = {
            "title": "Produto Teste",
            "price": 59.90,
            "commission": 15.0,
            "affiliate_url": "https://example.com/aff",
            "rating": 4.5,
            "category": "beauty",
            "available": True,
            "source_platform": "amazon",
        }
        defaults.update(overrides)
        return defaults

    def test_validate_product_valid(self):
        """A product that meets all criteria should be validated as True."""
        raw = self._make_raw()
        assert _validate_product(raw) is True

    def test_validate_product_low_commission(self):
        """A product with commission below both MIN_COMMISSION_BRL and MIN_COMMISSION_PCT should fail."""
        raw = self._make_raw(commission=1.0)
        assert _validate_product(raw) is False

    def test_validate_product_low_rating(self):
        """A product with rating below 4.3 should fail validation."""
        raw = self._make_raw(rating=3.9)
        assert _validate_product(raw) is False

    def test_validate_product_bad_price_too_low(self):
        """A product with price below the minimum range should fail."""
        # Physical product min is 30.0 (settings default)
        raw = self._make_raw(price=10.0, source_platform="amazon")
        assert _validate_product(raw) is False

    def test_validate_product_bad_price_too_high(self):
        """A product with price above the maximum range should fail."""
        # Physical product max is 150.0 (settings default)
        raw = self._make_raw(price=500.0, source_platform="amazon")
        assert _validate_product(raw) is False

    def test_validate_product_unavailable(self):
        """An unavailable product should fail validation."""
        raw = self._make_raw(available=False)
        assert _validate_product(raw) is False

    def test_validate_product_disallowed_category(self):
        """A product with a category not in ALLOWED_CATEGORIES should fail."""
        raw = self._make_raw(category="weapons")
        assert _validate_product(raw) is False

    def test_validate_product_digital_price_range(self):
        """A digital product (hotmart) should use the digital price range."""
        # Digital max is 297.0 (settings default)
        raw = self._make_raw(price=250.0, source_platform="hotmart", category="education")
        assert _validate_product(raw) is True

    def test_validate_product_rating_boundary(self):
        """A product with rating exactly at MIN_RATING (4.3) should pass."""
        raw = self._make_raw(rating=4.3)
        assert _validate_product(raw) is True


# ===================================================================
# _calc_commercial_score
# ===================================================================


class TestCalcCommercialScore:
    """Test the composite commercial score calculation."""

    def _make_trend_mock(self, name="skincare coreano", category="beauty"):
        """Create a mock Trend object for score calculation."""
        trend = MagicMock()
        trend.name = name
        trend.category = category
        return trend

    def test_calc_commercial_score_high(self):
        """A product with high commission, rating, and good price should score well."""
        raw = {
            "title": "Produto Premium de Skincare",
            "price": 79.90,
            "commission": 30.0,
            "rating": 4.8,
            "category": "skincare",
        }
        trend = self._make_trend_mock(name="skincare coreano", category="beauty")

        score = _calc_commercial_score(raw, trend)

        # commission=30 -> comissao=100 (35%)
        # rating=4.8 -> avaliacao=100 (20%)
        # price=79.90 -> preco=100 (20%) [50-100 range]
        # relevancia depends on TF-IDF, will be >= 0

        # Minimum without relevance: 100*0.35 + 100*0.20 + 100*0.20 = 75.0
        assert score >= 75.0

    def test_calc_commercial_score_low(self):
        """A product with poor metrics should score low."""
        raw = {
            "title": "Produto generico",
            "price": 200.0,
            "commission": 3.0,
            "rating": 4.0,
            "category": "outros",
        }
        trend = self._make_trend_mock(name="tendencia diferente", category="tech")

        score = _calc_commercial_score(raw, trend)

        # commission=3 -> comissao=0 (35%)
        # rating=4.0 -> avaliacao=0 (20%)
        # price=200 -> preco=40 (20%) [150-297 range]
        # relevancia likely very low (different topics)

        # Maximum: 0*0.35 + 0*0.20 + 40*0.20 = 8.0 + small relevancia
        assert score <= 30.0

    def test_calc_commercial_score_medium_commission(self):
        """Commission between 10-20 should yield comissao_score = 60."""
        raw = {
            "title": "Produto Medio de Beleza",
            "price": 75.0,
            "commission": 15.0,
            "rating": 4.5,
            "category": "beauty",
        }
        trend = self._make_trend_mock()

        score = _calc_commercial_score(raw, trend)

        # commission=15 -> comissao=60 (35%)
        # rating=4.5 -> avaliacao=80 (20%)
        # price=75 -> preco=100 (20%)
        # Expected base (without relevancia): 60*0.35 + 80*0.20 + 100*0.20 = 21+16+20 = 57
        assert score >= 50.0

    def test_calc_commercial_score_returns_float(self):
        """The score should always be a float."""
        raw = {
            "title": "Produto",
            "price": 50.0,
            "commission": 10.0,
            "rating": 4.3,
            "category": "beauty",
        }
        trend = self._make_trend_mock()

        score = _calc_commercial_score(raw, trend)
        assert isinstance(score, float)


# ===================================================================
# check_weekly_limit
# ===================================================================


class TestCheckWeeklyLimit:
    """Test the weekly limit enforcement for product script generation."""

    @patch("services.product_service.get_session_cm")
    def test_check_weekly_limit_under(self, mock_session_cm):
        """When script count is under the limit, should return True."""
        # Mock the session context manager and query result
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # Simulate 2 scripts this week (under the default limit of 5)
        mock_session.execute.return_value.scalar_one.return_value = 2

        result = check_weekly_limit(product_id=1)
        assert result is True

    @patch("services.product_service.get_session_cm")
    def test_check_weekly_limit_over(self, mock_session_cm):
        """When script count is at or over the limit, should return False."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # Simulate 5 scripts this week (at the default limit of 5)
        mock_session.execute.return_value.scalar_one.return_value = 5

        result = check_weekly_limit(product_id=1)
        assert result is False

    @patch("services.product_service.get_session_cm")
    def test_check_weekly_limit_exactly_at_limit(self, mock_session_cm):
        """When count equals the limit, should return False (>= is not under)."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # MAX_VIDEOS_PER_PRODUCT_WEEK defaults to 5; count of 5 means at limit
        mock_session.execute.return_value.scalar_one.return_value = 5

        result = check_weekly_limit(product_id=1)
        assert result is False

    @patch("services.product_service.get_session_cm")
    def test_check_weekly_limit_zero(self, mock_session_cm):
        """When no scripts exist this week, should return True."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_session.execute.return_value.scalar_one.return_value = 0

        result = check_weekly_limit(product_id=1)
        assert result is True

    @patch("services.product_service.get_session_cm")
    def test_check_weekly_limit_error_returns_false(self, mock_session_cm):
        """On database error, fail-safe should deny (return False)."""
        mock_session_cm.return_value.__enter__ = MagicMock(
            side_effect=Exception("DB connection failed")
        )
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        result = check_weekly_limit(product_id=1)
        assert result is False
