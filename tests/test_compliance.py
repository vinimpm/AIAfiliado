"""Tests for the compliance service.

Covers blocklist checking, CTA compatibility validation, and
duration estimation logic.
"""


from services.compliance import (
    check_blocklist,
    check_cta_compatibility,
    check_duration,
)

# ===================================================================
# check_blocklist
# ===================================================================


class TestCheckBlocklist:
    def test_blocklist_hard_match(self):
        """Text containing a hard-blocklist phrase should fail with 'passed' = False."""
        text = "Esse produto cura todas as suas dores de cabeca"
        result = check_blocklist(text)
        assert result["passed"] is False
        assert "cura" in result["hard_matches"]

    def test_blocklist_hard_no_match(self):
        """Clean text with no blocklist terms should pass."""
        text = "Esse produto e muito bom e eu gosto bastante dele"
        result = check_blocklist(text)
        assert result["passed"] is True
        assert result["hard_matches"] == []
        assert result["soft_matches"] == []

    def test_blocklist_soft_match(self):
        """Text with a soft-blocklist phrase should pass but have soft_matches populated."""
        text = "Esse produto e milagroso para a sua pele"
        result = check_blocklist(text)
        assert result["passed"] is True
        assert "milagroso" in result["soft_matches"]

    def test_blocklist_hard_multiple(self):
        """Multiple hard-blocklist matches should all appear in the result."""
        text = "Esse remedio cura doencas e da lucro garantido"
        result = check_blocklist(text)
        assert result["passed"] is False
        assert "cura" in result["hard_matches"]
        assert "remedio" in result["hard_matches"]
        assert "lucro garantido" in result["hard_matches"]

    def test_blocklist_case_insensitive(self):
        """Blocklist matching should be case-insensitive."""
        text = "CURA tudo rapidamente"
        result = check_blocklist(text)
        assert result["passed"] is False
        assert "cura" in result["hard_matches"]

    def test_blocklist_empty_text(self):
        """Empty text should pass the blocklist check."""
        result = check_blocklist("")
        assert result["passed"] is True
        assert result["hard_matches"] == []
        assert result["soft_matches"] == []

    def test_blocklist_soft_and_hard(self):
        """Text with both hard and soft matches should fail (hard takes priority)."""
        text = "Esse remedio milagroso funciona para todos"
        result = check_blocklist(text)
        assert result["passed"] is False
        assert "remedio" in result["hard_matches"]
        assert "funciona para todos" in result["hard_matches"]
        assert "milagroso" in result["soft_matches"]


# ===================================================================
# check_cta_compatibility
# ===================================================================


class TestCheckCtaCompatibility:
    def test_cta_tiktok_valid(self):
        """A 'link na bio' CTA for TikTok should be accepted."""
        assert check_cta_compatibility("Confira o link na bio!", "tiktok") is True

    def test_cta_tiktok_invalid(self):
        """A CTA containing 'http://' for TikTok should be rejected."""
        assert check_cta_compatibility("Acesse http://exemplo.com", "tiktok") is False

    def test_cta_tiktok_prohibited_www(self):
        """A CTA containing 'www' for TikTok should be rejected."""
        assert check_cta_compatibility("Acesse www.exemplo.com", "tiktok") is False

    def test_cta_tiktok_prohibited_dotcom(self):
        """A CTA containing '.com' for TikTok should be rejected."""
        assert check_cta_compatibility("Acesse exemplo.com", "tiktok") is False

    def test_cta_instagram_valid(self):
        """A 'link na bio' CTA for Instagram should be accepted."""
        assert check_cta_compatibility("link na bio para saber mais", "instagram") is True

    def test_cta_instagram_invalid(self):
        """A CTA containing 'http' for Instagram should be rejected."""
        assert check_cta_compatibility("Veja em http://example.com", "instagram") is False

    def test_cta_youtube_valid(self):
        """A 'link na descricao' CTA for YouTube should be accepted."""
        assert check_cta_compatibility("link na descricao", "youtube") is True

    def test_cta_youtube_allows_links(self):
        """YouTube has no prohibited terms so direct URLs should be allowed."""
        assert check_cta_compatibility("Acesse http://produto.com", "youtube") is True

    def test_cta_unknown_platform(self):
        """An unknown platform should default to allowing the CTA (fail-safe)."""
        assert check_cta_compatibility("qualquer coisa", "twitter") is True


# ===================================================================
# check_duration
# ===================================================================


class TestCheckDuration:
    def test_duration_too_short(self):
        """A script with only 5 words (~2s) should be rejected as too short."""
        # 5 words / 2.5 = 2 seconds -- below the 20s minimum
        hook = "Ola"
        body = "teste curto"
        cta = "aqui agora"
        assert check_duration(hook, body, cta) is False

    def test_duration_valid(self):
        """A script with ~75 words (~30s) should pass duration validation."""
        # ~75 words / 2.5 = 30 seconds -- within the 20-45s range
        hook = "Voce sabia que o skincare coreano esta dominando o TikTok"
        body = (
            "Esse produto incrivel que todo mundo esta comentando nos ultimos dias "
            "chegou ao Brasil e esta mudando a rotina de cuidados com a pele de "
            "milhares de pessoas que buscam uma pele mais saudavel e bonita todos "
            "os dias usando ingredientes naturais e tecnologia avancada da Coreia "
            "do Sul para resultados visiveis em poucas semanas de uso continuo"
        )
        cta = "Confira o link na bio para saber mais sobre esse produto"

        full_text = " ".join([hook, body, cta])
        word_count = len(full_text.split())
        estimated_seconds = word_count / 2.5

        # Verify the estimated duration is within range
        assert 20.0 <= estimated_seconds <= 45.0, (
            f"Test word count {word_count} yields {estimated_seconds}s, expected 20-45s"
        )
        assert check_duration(hook, body, cta) is True

    def test_duration_too_long(self):
        """A script with ~200 words (~80s) should be rejected as too long."""
        # 200 words / 2.5 = 80 seconds -- above the 45s maximum
        hook = "Voce sabia disso"
        body = " ".join(["palavra"] * 190)
        cta = "confira agora mesmo link na bio"
        assert check_duration(hook, body, cta) is False

    def test_duration_lower_boundary(self):
        """A script with exactly 50 words (20s) should pass."""
        # 50 words / 2.5 = 20 seconds -- exactly on the lower boundary
        words = " ".join(["palavra"] * 50)
        assert check_duration(words, "", "") is True

    def test_duration_upper_boundary(self):
        """A script with 112 words (~44.8s) should pass."""
        # 112 words / 2.5 = 44.8 seconds -- just under 45s
        words = " ".join(["palavra"] * 112)
        assert check_duration(words, "", "") is True

    def test_duration_just_over_upper(self):
        """A script with 113 words (~45.2s) should fail (above 45s)."""
        # 113 words / 2.5 = 45.2 seconds -- just over 45s
        words = " ".join(["palavra"] * 113)
        assert check_duration(words, "", "") is False
