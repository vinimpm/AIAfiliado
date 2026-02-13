"""Tests for the account_health service scoring logic.

Tests the pure scoring functions, decay formula, and risk-level
classification -- NOT the Celery task or database-dependent collectors.
"""

import pytest

from services.account_health import (
    _apply_decay,
    _classify_risk,
    _score_cadence,
    _score_reach_drop,
    _score_removals,
    _score_similarity,
    _score_strikes,
)

# ===================================================================
# _score_removals
# ===================================================================


class TestScoreRemovals:
    def test_score_removals_zero(self):
        """0 removals should produce a risk score of 0."""
        assert _score_removals(0) == 0

    def test_score_removals_one(self):
        """1 removal should produce a risk score of 40."""
        assert _score_removals(1) == 40

    def test_score_removals_multiple(self):
        """3 removals (>= 3) should produce the maximum risk score of 100."""
        assert _score_removals(3) == 100

    def test_score_removals_two(self):
        """2 removals should produce a risk score of 70."""
        assert _score_removals(2) == 70

    def test_score_removals_negative(self):
        """Negative input should be treated as zero removals."""
        assert _score_removals(-1) == 0


# ===================================================================
# _score_strikes
# ===================================================================


class TestScoreStrikes:
    def test_score_strikes_zero(self):
        """0 strikes should produce a risk score of 0."""
        assert _score_strikes(0) == 0

    def test_score_strikes_one(self):
        """1 strike should produce a risk score of 60."""
        assert _score_strikes(1) == 60

    def test_score_strikes_multiple(self):
        """2 or more strikes should produce the maximum risk score of 100."""
        assert _score_strikes(2) == 100
        assert _score_strikes(5) == 100


# ===================================================================
# _score_reach_drop
# ===================================================================


class TestScoreReachDrop:
    def test_score_reach_drop_none(self):
        """A drop ratio of 0 (no drop) should produce a risk score of 0."""
        assert _score_reach_drop(0.0) == 0

    def test_score_reach_drop_moderate(self):
        """A drop ratio of 0.3 should produce a risk score of 30."""
        assert _score_reach_drop(0.3) == 30

    def test_score_reach_drop_significant(self):
        """A drop ratio of 0.5 should produce a risk score of 60."""
        assert _score_reach_drop(0.5) == 60

    def test_score_reach_drop_severe(self):
        """A drop ratio of 0.6 (> 0.5) should produce the maximum risk score of 100."""
        assert _score_reach_drop(0.6) == 100

    def test_score_reach_drop_negative(self):
        """A negative drop ratio (views increased) should produce a risk score of 0."""
        assert _score_reach_drop(-0.2) == 0


# ===================================================================
# _score_similarity
# ===================================================================


class TestScoreSimilarity:
    def test_score_similarity_low(self):
        """Similarity of 0.4 (<= 0.5) should produce a risk score of 0."""
        assert _score_similarity(0.4) == 0

    def test_score_similarity_medium(self):
        """Similarity of 0.6 (0.5 < x <= 0.7) should produce a risk score of 40."""
        assert _score_similarity(0.6) == 40

    def test_score_similarity_medium_high(self):
        """Similarity of 0.8 (0.7 < x <= 0.85) should produce a risk score of 70."""
        assert _score_similarity(0.8) == 70

    def test_score_similarity_high(self):
        """Similarity of 0.9 (> 0.85) should produce the maximum risk score of 100."""
        assert _score_similarity(0.9) == 100

    def test_score_similarity_boundary_low(self):
        """Similarity of exactly 0.5 should be in the 0 bucket."""
        assert _score_similarity(0.5) == 0


# ===================================================================
# _score_cadence
# ===================================================================


class TestScoreCadence:
    def test_score_cadence_normal(self):
        """5 posts in 7 days (<= 7) should produce a risk score of 0."""
        assert _score_cadence(5) == 0

    def test_score_cadence_moderate(self):
        """10 posts in 7 days (7 < x <= 14) should produce a risk score of 30."""
        assert _score_cadence(10) == 30

    def test_score_cadence_high(self):
        """18 posts in 7 days (14 < x <= 21) should produce a risk score of 60."""
        assert _score_cadence(18) == 60

    def test_score_cadence_excessive(self):
        """25 posts in 7 days (> 21) should produce the maximum risk score of 100."""
        assert _score_cadence(25) == 100

    def test_score_cadence_zero(self):
        """0 posts should produce a risk score of 0."""
        assert _score_cadence(0) == 0


# ===================================================================
# _apply_decay
# ===================================================================


class TestApplyDecay:
    def test_apply_decay_zero_days(self):
        """With 0 days elapsed, the score should remain unchanged."""
        assert _apply_decay(100.0, 0.0) == 100.0

    def test_apply_decay_one_day(self):
        """After 1 day, score should be reduced by 10%: 100 * 0.9 = 90."""
        result = _apply_decay(100.0, 1.0)
        assert result == pytest.approx(90.0)

    def test_apply_decay_five_days(self):
        """After 5 days, score should be 100 * (1 - 5*0.1) = 50."""
        result = _apply_decay(100.0, 5.0)
        assert result == pytest.approx(50.0)

    def test_apply_decay_ten_days(self):
        """After 10 days, the score should be fully decayed to 0."""
        result = _apply_decay(100.0, 10.0)
        assert result == pytest.approx(0.0)

    def test_apply_decay_clamps_to_zero(self):
        """After more than 10 days, the score should never go below 0."""
        result = _apply_decay(100.0, 15.0)
        assert result == 0.0

    def test_apply_decay_partial_day(self):
        """Fractional days should be handled correctly (e.g. 2.5 days)."""
        result = _apply_decay(80.0, 2.5)
        # 80 * (1 - 2.5 * 0.1) = 80 * 0.75 = 60
        assert result == pytest.approx(60.0)

    def test_apply_decay_zero_score(self):
        """A score of 0 should remain 0 regardless of days."""
        assert _apply_decay(0.0, 5.0) == 0.0


# ===================================================================
# _classify_risk
# ===================================================================


class TestClassifyRisk:
    def test_risk_level_low(self):
        """A score of 20 (<= 34) should classify as LOW."""
        assert _classify_risk(20) == "LOW"

    def test_risk_level_low_boundary(self):
        """A score of exactly 34 should classify as LOW."""
        assert _classify_risk(34) == "LOW"

    def test_risk_level_medium(self):
        """A score of 50 (34 < x <= 64) should classify as MEDIUM."""
        assert _classify_risk(50) == "MEDIUM"

    def test_risk_level_medium_boundary(self):
        """A score of exactly 64 should classify as MEDIUM."""
        assert _classify_risk(64) == "MEDIUM"

    def test_risk_level_high(self):
        """A score of 80 (> 64) should classify as HIGH."""
        assert _classify_risk(80) == "HIGH"

    def test_risk_level_max(self):
        """A score of 100 should classify as HIGH."""
        assert _classify_risk(100) == "HIGH"

    def test_risk_level_zero(self):
        """A score of 0 should classify as LOW."""
        assert _classify_risk(0) == "LOW"
