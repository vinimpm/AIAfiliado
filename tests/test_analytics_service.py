"""Tests for the analytics_service module.

Covers pause detection, retirement detection, and A/B winner determination.
Uses pytest-mock (mocker) and unittest.mock to isolate database operations.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.analytics_service import (
    determine_ab_winner,
    should_pause,
    should_retire,
)


# ===================================================================
# should_pause
# ===================================================================


class TestShouldPause:
    """Test the should_pause function for detecting underperforming publications."""

    def _make_publication_mock(self, posted_at, status="POSTED"):
        """Create a mock Publication object."""
        pub = MagicMock()
        pub.status = status
        pub.posted_at = posted_at
        return pub

    def _make_metric_mock(self, retention_3s=None, views=0, clicks=0, sales=0):
        """Create a mock Metric object."""
        metric = MagicMock()
        metric.retention_3s = retention_3s
        metric.views = views
        metric.clicks = clicks
        metric.sales = sales
        return metric

    @patch("services.analytics_service.get_session_cm")
    def test_should_pause_low_retention(self, mock_session_cm):
        """A publication with retention 0.1 after 24h should be paused."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # Publication posted 30 hours ago
        posted_at = datetime.now(timezone.utc) - timedelta(hours=30)
        pub = self._make_publication_mock(posted_at=posted_at)
        metric = self._make_metric_mock(retention_3s=0.1, views=500)

        # First query returns the publication, second returns the metric
        mock_session.execute.return_value.scalar_one_or_none.side_effect = [pub, metric]

        result = should_pause(publication_id=1)
        assert result is True

    @patch("services.analytics_service.get_session_cm")
    def test_should_pause_low_views(self, mock_session_cm):
        """A publication with only 50 views after 24h should be paused."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        posted_at = datetime.now(timezone.utc) - timedelta(hours=30)
        pub = self._make_publication_mock(posted_at=posted_at)
        metric = self._make_metric_mock(retention_3s=0.5, views=50)

        mock_session.execute.return_value.scalar_one_or_none.side_effect = [pub, metric]

        result = should_pause(publication_id=1)
        assert result is True

    @patch("services.analytics_service.get_session_cm")
    def test_should_pause_good_metrics(self, mock_session_cm):
        """A publication with high retention and views should NOT be paused."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        posted_at = datetime.now(timezone.utc) - timedelta(hours=30)
        pub = self._make_publication_mock(posted_at=posted_at)
        metric = self._make_metric_mock(retention_3s=0.75, views=5000)

        mock_session.execute.return_value.scalar_one_or_none.side_effect = [pub, metric]

        result = should_pause(publication_id=1)
        assert result is False

    @patch("services.analytics_service.get_session_cm")
    def test_should_pause_too_early(self, mock_session_cm):
        """A publication posted less than 24h ago should NOT be paused (too early to judge)."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        posted_at = datetime.now(timezone.utc) - timedelta(hours=12)
        pub = self._make_publication_mock(posted_at=posted_at)

        mock_session.execute.return_value.scalar_one_or_none.return_value = pub

        result = should_pause(publication_id=1)
        assert result is False

    @patch("services.analytics_service.get_session_cm")
    def test_should_pause_not_found(self, mock_session_cm):
        """If publication is not found, should_pause returns False."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = should_pause(publication_id=999)
        assert result is False


# ===================================================================
# should_retire
# ===================================================================


class TestShouldRetire:
    """Test the should_retire function for detecting inactive products."""

    @patch("services.analytics_service.get_session_cm")
    def test_should_retire_no_sales(self, mock_session_cm):
        """A product with no sales in 7 days should be retired."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # Create a mock product that is active
        product = MagicMock()
        product.is_active = True
        product.id = 1

        # Create mock call results
        # First execute: select(Product) -> returns the product
        # Second execute: select(Publication.id) -> returns pub_ids
        # Third execute: select(func.sum(Metric.sales)) -> returns 0
        mock_results = [
            MagicMock(),  # select(Product)
            MagicMock(),  # select(Publication.id)
            MagicMock(),  # select(func.sum(Metric.sales))
        ]
        mock_results[0].scalar_one_or_none.return_value = product
        mock_results[1].scalars.return_value.all.return_value = [1, 2, 3]
        mock_results[2].scalar_one.return_value = 0

        mock_session.execute.side_effect = mock_results

        result = should_retire(product_id=1)
        assert result is True

    @patch("services.analytics_service.get_session_cm")
    def test_should_retire_recent_sales(self, mock_session_cm):
        """A product with recent sales should NOT be retired."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        product = MagicMock()
        product.is_active = True
        product.id = 1

        mock_results = [
            MagicMock(),  # select(Product)
            MagicMock(),  # select(Publication.id)
            MagicMock(),  # select(func.sum(Metric.sales))
        ]
        mock_results[0].scalar_one_or_none.return_value = product
        mock_results[1].scalars.return_value.all.return_value = [1, 2]
        mock_results[2].scalar_one.return_value = 5  # 5 recent sales

        mock_session.execute.side_effect = mock_results

        result = should_retire(product_id=1)
        assert result is False

    @patch("services.analytics_service.get_session_cm")
    def test_should_retire_inactive_product(self, mock_session_cm):
        """An already inactive product should return False (nothing to retire)."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        product = MagicMock()
        product.is_active = False

        mock_session.execute.return_value.scalar_one_or_none.return_value = product

        result = should_retire(product_id=1)
        assert result is False

    @patch("services.analytics_service.get_session_cm")
    def test_should_retire_no_publications(self, mock_session_cm):
        """An active product with no publications at all should be retired."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        product = MagicMock()
        product.is_active = True
        product.id = 1

        mock_results = [
            MagicMock(),  # select(Product)
            MagicMock(),  # select(Publication.id)
        ]
        mock_results[0].scalar_one_or_none.return_value = product
        mock_results[1].scalars.return_value.all.return_value = []  # no publications

        mock_session.execute.side_effect = mock_results

        result = should_retire(product_id=1)
        assert result is True

    @patch("services.analytics_service.get_session_cm")
    def test_should_retire_product_not_found(self, mock_session_cm):
        """If the product is not found, should_retire returns False."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        result = should_retire(product_id=999)
        assert result is False


# ===================================================================
# determine_ab_winner
# ===================================================================


class TestDetermineAbWinner:
    """Test A/B winner determination based on CTR comparison."""

    @patch("services.analytics_service.get_session_cm")
    def test_determine_ab_winner_a_wins(self, mock_session_cm):
        """When variant A has a higher CTR, it should be returned as the winner."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        # Build separate mock results for each execute() call:
        # Call 1: variant A publication ids
        # Call 2: variant A metric (for pub_id)
        # Call 3: variant B publication ids
        # Call 4: variant B metric (for pub_id)

        metric_a = MagicMock()
        metric_a.views = 1000
        metric_a.clicks = 100  # CTR = 0.10

        metric_b = MagicMock()
        metric_b.views = 1000
        metric_b.clicks = 50  # CTR = 0.05

        call_count = 0

        def side_effect_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()

            if call_count == 1:
                # Variant A publication IDs
                mock_result.scalars.return_value.all.return_value = [10]
            elif call_count == 2:
                # Variant A latest metric
                mock_result.scalar_one_or_none.return_value = metric_a
            elif call_count == 3:
                # Variant B publication IDs
                mock_result.scalars.return_value.all.return_value = [20]
            elif call_count == 4:
                # Variant B latest metric
                mock_result.scalar_one_or_none.return_value = metric_b

            return mock_result

        mock_session.execute.side_effect = side_effect_execute

        result = determine_ab_winner(product_id=1, hours_threshold=48)
        assert result == "A"

    @patch("services.analytics_service.get_session_cm")
    def test_determine_ab_winner_b_wins(self, mock_session_cm):
        """When variant B has a higher CTR, it should be returned as the winner."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        metric_a = MagicMock()
        metric_a.views = 1000
        metric_a.clicks = 30  # CTR = 0.03

        metric_b = MagicMock()
        metric_b.views = 1000
        metric_b.clicks = 80  # CTR = 0.08

        call_count = 0

        def side_effect_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()

            if call_count == 1:
                mock_result.scalars.return_value.all.return_value = [10]
            elif call_count == 2:
                mock_result.scalar_one_or_none.return_value = metric_a
            elif call_count == 3:
                mock_result.scalars.return_value.all.return_value = [20]
            elif call_count == 4:
                mock_result.scalar_one_or_none.return_value = metric_b

            return mock_result

        mock_session.execute.side_effect = side_effect_execute

        result = determine_ab_winner(product_id=1, hours_threshold=48)
        assert result == "B"

    @patch("services.analytics_service.get_session_cm")
    def test_determine_ab_winner_insufficient_data(self, mock_session_cm):
        """When only one variant has data, result should be None."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        metric_a = MagicMock()
        metric_a.views = 1000
        metric_a.clicks = 100

        call_count = 0

        def side_effect_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()

            if call_count == 1:
                # Variant A has publications
                mock_result.scalars.return_value.all.return_value = [10]
            elif call_count == 2:
                mock_result.scalar_one_or_none.return_value = metric_a
            elif call_count == 3:
                # Variant B has NO publications
                mock_result.scalars.return_value.all.return_value = []

            return mock_result

        mock_session.execute.side_effect = side_effect_execute

        result = determine_ab_winner(product_id=1, hours_threshold=48)
        assert result is None

    @patch("services.analytics_service.get_session_cm")
    def test_determine_ab_winner_tie(self, mock_session_cm):
        """When both variants have equal CTR, result should be None (tie)."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        metric_a = MagicMock()
        metric_a.views = 1000
        metric_a.clicks = 50  # CTR = 0.05

        metric_b = MagicMock()
        metric_b.views = 1000
        metric_b.clicks = 50  # CTR = 0.05

        call_count = 0

        def side_effect_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()

            if call_count == 1:
                mock_result.scalars.return_value.all.return_value = [10]
            elif call_count == 2:
                mock_result.scalar_one_or_none.return_value = metric_a
            elif call_count == 3:
                mock_result.scalars.return_value.all.return_value = [20]
            elif call_count == 4:
                mock_result.scalar_one_or_none.return_value = metric_b

            return mock_result

        mock_session.execute.side_effect = side_effect_execute

        result = determine_ab_winner(product_id=1, hours_threshold=48)
        assert result is None

    @patch("services.analytics_service.get_session_cm")
    def test_determine_ab_winner_zero_views(self, mock_session_cm):
        """When both variants have 0 views and 0 clicks, result should be None."""
        mock_session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

        metric_a = MagicMock()
        metric_a.views = 0
        metric_a.clicks = 0

        metric_b = MagicMock()
        metric_b.views = 0
        metric_b.clicks = 0

        call_count = 0

        def side_effect_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()

            if call_count == 1:
                mock_result.scalars.return_value.all.return_value = [10]
            elif call_count == 2:
                mock_result.scalar_one_or_none.return_value = metric_a
            elif call_count == 3:
                mock_result.scalars.return_value.all.return_value = [20]
            elif call_count == 4:
                mock_result.scalar_one_or_none.return_value = metric_b

            return mock_result

        mock_session.execute.side_effect = side_effect_execute

        # When total_views is 0 for both, the variant_stats won't include them
        # because of the `if total_views > 0:` guard in the source code,
        # so the result should be None (insufficient data)
        result = determine_ab_winner(product_id=1, hours_threshold=48)
        assert result is None
