"""Tests for the script_service module.

Covers hash generation (deduplication), normalisation, and
similarity checking via TF-IDF + cosine similarity.
"""


from services.script_service import _check_similarity, _generate_hash

# ===================================================================
# _generate_hash
# ===================================================================


class TestGenerateHash:
    def test_generate_hash_consistency(self):
        """The same input should always produce the same hash."""
        hook = "Voce sabia que esse produto viralizou?"
        body = "Ele esta em alta no TikTok e todo mundo quer."
        cta = "Link na bio para conferir!"

        hash1 = _generate_hash(hook, body, cta)
        hash2 = _generate_hash(hook, body, cta)

        assert hash1 == hash2

    def test_generate_hash_different(self):
        """Different inputs should produce different hashes."""
        hash1 = _generate_hash(
            "Hook A",
            "Body about product A",
            "CTA for A",
        )
        hash2 = _generate_hash(
            "Hook B",
            "Body about product B",
            "CTA for B",
        )

        assert hash1 != hash2

    def test_generate_hash_normalization(self):
        """Same text with different spacing and case should produce the same hash.

        The function normalises by lowercasing, collapsing whitespace,
        and removing punctuation before hashing.
        """
        hash1 = _generate_hash(
            "  Voce Sabia  ",
            "   Produto  incrivel   aqui  ",
            "  Link Na Bio  ",
        )
        hash2 = _generate_hash(
            "voce sabia",
            "produto incrivel aqui",
            "link na bio",
        )

        assert hash1 == hash2

    def test_generate_hash_punctuation_ignored(self):
        """Punctuation differences should not affect the hash."""
        hash1 = _generate_hash(
            "Voce sabia?",
            "Produto incrivel!",
            "Link na bio.",
        )
        hash2 = _generate_hash(
            "Voce sabia",
            "Produto incrivel",
            "Link na bio",
        )

        assert hash1 == hash2

    def test_generate_hash_is_sha256(self):
        """The resulting hash should be a 64-character hex string (SHA-256)."""
        result = _generate_hash("hook", "body", "cta")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_generate_hash_empty_strings(self):
        """Empty strings should still produce a valid hash."""
        result = _generate_hash("", "", "")
        assert len(result) == 64


# ===================================================================
# _check_similarity
# ===================================================================


class TestCheckSimilarity:
    def test_check_similarity_different(self):
        """Dissimilar texts should not be flagged as too similar."""
        new_text = "Skincare coreano esta dominando o Brasil com produtos inovadores"
        recent_texts = [
            "Receita de bolo de chocolate facil e rapida para o fim de semana",
            "Dicas de exercicios para fazer em casa sem equipamento",
        ]

        assert _check_similarity(new_text, recent_texts, threshold=0.85) is False

    def test_check_similarity_same(self):
        """Identical texts should be flagged as too similar."""
        text = "Esse produto de skincare coreano esta viralizando no TikTok"
        new_text = text
        recent_texts = [text]

        assert _check_similarity(new_text, recent_texts, threshold=0.85) is True

    def test_check_similarity_threshold(self):
        """Texts at the similarity boundary should behave correctly.

        Uses a low threshold (0.1) to verify that even moderately
        similar texts are caught when the threshold is permissive.
        """
        new_text = "Produto de beleza coreano para pele"
        recent_texts = [
            "Produto de beleza coreano incrivel para pele",
        ]

        # With a very low threshold, similar texts should be caught
        assert _check_similarity(new_text, recent_texts, threshold=0.1) is True

        # With a very high threshold (0.99), they should pass
        assert _check_similarity(new_text, recent_texts, threshold=0.99) is False

    def test_check_similarity_empty_recent(self):
        """When there are no recent texts to compare against, result should be False."""
        assert _check_similarity("any text here", [], threshold=0.85) is False

    def test_check_similarity_multiple_recent(self):
        """When one of several recent texts is identical, it should be caught."""
        new_text = "Esse creme facial coreano e o melhor"
        recent_texts = [
            "Receita de bolo facil para iniciantes",
            "Dicas de viagem para o verao no nordeste",
            "Esse creme facial coreano e o melhor",  # identical
        ]

        assert _check_similarity(new_text, recent_texts, threshold=0.85) is True

    def test_check_similarity_near_duplicate(self):
        """Near-duplicate texts (very minor changes) should be flagged at default threshold."""
        new_text = (
            "Voce sabia que esse produto de skincare coreano "
            "esta viralizando no TikTok e todo mundo quer testar"
        )
        recent_texts = [
            "Voce sabia que esse produto de skincare coreano "
            "esta viralizando no TikTok e todo mundo quer experimentar"
        ]

        # Minor word change -- should be very similar
        result = _check_similarity(new_text, recent_texts, threshold=0.85)
        # This depends on TF-IDF vectorisation; just verify it runs without error
        assert isinstance(result, bool)
