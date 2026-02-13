"""Script Generation Service -- creates video scripts for affiliate products.

Responsibilities:
    - Generate A/B variant scripts for a given product + trend combination.
    - Call LLM API (OpenAI or Anthropic) to produce structured script JSON.
    - Deduplicate scripts via SHA-256 hashing.
    - Check similarity against recent scripts via TF-IDF + cosine similarity.
    - Validate scripts through the compliance pipeline.
    - Persist approved scripts to the database.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import openai
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select

from app.celery_app import celery
from app.config import settings
from app.logging import get_logger
from models.database import get_session_cm
from models.product import Product
from models.script import Script
from models.trend import Trend
from services.compliance import validate_script

logger = get_logger(service="script_service")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

SCRIPT_GENERATION_PROMPT = """Voce e um redator especializado em roteiros curtos para videos de afiliados
em redes sociais no Brasil. Crie um roteiro persuasivo e natural.

PLATAFORMA: {platform}
TREND: {trend_name} (categoria: {trend_category})
PRODUTO: {product_title} (preco: R$ {product_price})
INSTRUCAO DE CTA: {cta_instruction}
DURACAO ALVO: {duration_range}

{variant_modifier}

FORMATO DE RESPOSTA (JSON):
{{
  "hook": "frase de abertura que prende atencao (3-5 segundos)",
  "body": "desenvolvimento do argumento de venda conectando a trend ao produto (15-25 segundos)",
  "cta": "chamada para acao final ({cta_instruction})",
  "caption": "legenda para postagem com hashtags relevantes",
  "estimated_duration_seconds": 30
}}

REGRAS:
- O roteiro deve ser em portugues brasileiro informal e natural.
- Conecte a trend ao produto de forma organica.
- NAO faca alegacoes medicas, financeiras ou garantias absolutas.
- NAO use falsa escassez ou falso endosso.
- O tom deve ser autentico, como um amigo recomendando algo.
- Inclua 3-5 hashtags relevantes na caption.
- A duracao estimada deve ficar entre 20 e 45 segundos.

Responda APENAS com o JSON, sem texto adicional.
"""

# ---------------------------------------------------------------------------
# CTA instructions per platform
# ---------------------------------------------------------------------------

CTA_INSTRUCTIONS: dict[str, str] = {
    "tiktok": "link na bio ou TikTok Shop",
    "instagram": "link na bio ou stories",
    "youtube": "link na descricao",
}

# ---------------------------------------------------------------------------
# A/B variant modifiers
# ---------------------------------------------------------------------------

VARIANT_MODIFIERS: dict[str, str] = {
    "A": (
        "ESTILO VARIANTE A:\n"
        "- Use um hook no formato de PERGUNTA que gere curiosidade.\n"
        "- Ex: 'Voce sabia que...?', 'Ja ouviu falar de...?'\n"
        "- O CTA deve transmitir URGENCIA suave (sem falsa escassez).\n"
        "- Ex: 'Aproveita enquanto esta disponivel', 'Nao deixa passar'"
    ),
    "B": (
        "ESTILO VARIANTE B:\n"
        "- Use um hook no formato de FATO SURPREENDENTE ou estatistica.\n"
        "- Ex: 'Milhares de pessoas ja...', 'Esse produto viralizou porque...'\n"
        "- O CTA deve usar PROVA SOCIAL.\n"
        "- Ex: 'Muita gente ja esta usando', 'Veja o que o pessoal esta falando'"
    ),
}


# ===========================================================================
# 1. generate_scripts -- main Celery task
# ===========================================================================


@celery.task(
    name="services.script_service.generate_scripts",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def generate_scripts(
    self,
    product_id: int,
    trend_id: int,
    platform: str,
    variant_count: int = 2,
) -> list[dict]:
    """Generate A/B variant scripts for a product + trend combination.

    Parameters
    ----------
    product_id:
        The product ID to generate scripts for.
    trend_id:
        The trend ID to connect the scripts to.
    platform:
        Target platform (``tiktok``, ``instagram``, ``youtube``).
    variant_count:
        Number of variants to generate (default: 2 for A/B).

    Returns
    -------
    list[dict]
        List of approved script dicts saved to the database.
    """
    logger.info(
        "generate_scripts.start",
        product_id=product_id,
        trend_id=trend_id,
        platform=platform,
        variant_count=variant_count,
    )

    variants = list(VARIANT_MODIFIERS.keys())[:variant_count]
    approved_scripts: list[dict] = []

    try:
        with get_session_cm() as session:
            # Load product and trend from DB
            product = session.execute(
                select(Product).where(Product.id == product_id)
            ).scalar_one_or_none()

            if product is None:
                logger.error(
                    "generate_scripts.product_not_found",
                    product_id=product_id,
                )
                return []

            trend = session.execute(select(Trend).where(Trend.id == trend_id)).scalar_one_or_none()

            if trend is None:
                logger.error(
                    "generate_scripts.trend_not_found",
                    trend_id=trend_id,
                )
                return []

            # Load recent scripts for similarity comparison
            recent_scripts = _load_recent_scripts(session, platform)

            for variant_key in variants:
                logger.info(
                    "generate_scripts.generating_variant",
                    variant=variant_key,
                    product_id=product_id,
                    trend_id=trend_id,
                )

                # Build the prompt
                prompt = SCRIPT_GENERATION_PROMPT.format(
                    platform=platform,
                    trend_name=trend.name,
                    trend_category=trend.category or "geral",
                    product_title=product.title,
                    product_price=f"{float(product.price):.2f}",
                    cta_instruction=CTA_INSTRUCTIONS.get(platform, "link na bio"),
                    duration_range="20-45 segundos",
                    variant_modifier=VARIANT_MODIFIERS[variant_key],
                )

                # Call LLM to generate the script
                retries = 0
                script_data = None
                while retries < settings.MAX_RETRIES_ON_DUPLICATE:
                    llm_response = _call_llm(prompt)
                    if llm_response is None:
                        logger.error(
                            "generate_scripts.llm_failed",
                            variant=variant_key,
                            attempt=retries + 1,
                        )
                        retries += 1
                        continue

                    hook = llm_response.get("hook", "")
                    body = llm_response.get("body", "")
                    cta = llm_response.get("cta", "")
                    caption = llm_response.get("caption", "")

                    if not hook or not body or not cta:
                        logger.warning(
                            "generate_scripts.incomplete_response",
                            variant=variant_key,
                            attempt=retries + 1,
                        )
                        retries += 1
                        continue

                    # Generate hash for deduplication
                    script_hash = _generate_hash(hook, body, cta)

                    # Check for exact duplicates in DB
                    existing = session.execute(
                        select(Script).where(Script.hash == script_hash)
                    ).scalar_one_or_none()

                    if existing is not None:
                        logger.info(
                            "generate_scripts.duplicate_hash",
                            variant=variant_key,
                            hash=script_hash[:16],
                            attempt=retries + 1,
                        )
                        retries += 1
                        # Add uniqueness instruction for retry
                        prompt += (
                            "\n\nIMPORTANTE: O roteiro anterior foi muito similar a um existente. "
                            "Crie algo completamente diferente e original."
                        )
                        continue

                    # Check similarity against recent scripts
                    new_text = f"{hook} {body} {cta}"
                    recent_texts = [f"{s['hook']} {s['body']} {s['cta']}" for s in recent_scripts]

                    if _check_similarity(
                        new_text,
                        recent_texts,
                        threshold=settings.SIMILARITY_THRESHOLD,
                    ):
                        logger.info(
                            "generate_scripts.too_similar",
                            variant=variant_key,
                            attempt=retries + 1,
                        )
                        retries += 1
                        prompt += (
                            "\n\nIMPORTANTE: O roteiro gerado e muito parecido com roteiros recentes. "
                            "Use uma abordagem, vocabulario e estrutura completamente diferentes."
                        )
                        continue

                    # Script is unique -- proceed with validation
                    script_data = {
                        "hook": hook,
                        "body": body,
                        "cta": cta,
                        "caption": caption,
                        "hash": script_hash,
                        "estimated_duration_seconds": llm_response.get(
                            "estimated_duration_seconds", 30
                        ),
                    }
                    break

                if script_data is None:
                    logger.warning(
                        "generate_scripts.variant_failed",
                        variant=variant_key,
                        product_id=product_id,
                        reason="max_retries_exhausted",
                    )
                    continue

                # Run compliance validation
                validation = validate_script(
                    hook=script_data["hook"],
                    body=script_data["body"],
                    cta=script_data["cta"],
                    caption=script_data["caption"],
                    platform=platform,
                )

                if not validation.get("approved", False):
                    logger.info(
                        "generate_scripts.compliance_rejected",
                        variant=variant_key,
                        reason=validation.get("reason", ""),
                        violations=validation.get("violations", []),
                        severity=validation.get("severity", ""),
                    )
                    continue

                # Save approved script to DB
                script_obj = Script(
                    product_id=product_id,
                    trend_id=trend_id,
                    platform=platform,
                    hook=script_data["hook"],
                    body=script_data["body"],
                    cta=script_data["cta"],
                    caption=script_data["caption"],
                    hash=script_data["hash"],
                    variant=variant_key,
                    status="approved",
                )
                session.add(script_obj)
                session.flush()

                script_dict = {
                    "id": script_obj.id,
                    "product_id": product_id,
                    "trend_id": trend_id,
                    "platform": platform,
                    "hook": script_obj.hook,
                    "body": script_obj.body,
                    "cta": script_obj.cta,
                    "caption": script_obj.caption,
                    "hash": script_obj.hash,
                    "variant": variant_key,
                    "status": script_obj.status,
                    "estimated_duration_seconds": script_data["estimated_duration_seconds"],
                }
                approved_scripts.append(script_dict)

                # Add to recent scripts cache for next variant comparison
                recent_scripts.append(
                    {
                        "hook": script_obj.hook,
                        "body": script_obj.body,
                        "cta": script_obj.cta,
                    }
                )

                logger.info(
                    "generate_scripts.variant_approved",
                    variant=variant_key,
                    script_id=script_obj.id,
                )

        logger.info(
            "generate_scripts.done",
            product_id=product_id,
            trend_id=trend_id,
            approved_count=len(approved_scripts),
        )
        return approved_scripts

    except Exception as exc:
        logger.error(
            "generate_scripts.error",
            product_id=product_id,
            trend_id=trend_id,
            error=str(exc),
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60)


# ===========================================================================
# 5. _generate_hash
# ===========================================================================


def _generate_hash(hook: str, body: str, cta: str) -> str:
    """Generate a SHA-256 hash for script deduplication.

    Normalises text by lowercasing, stripping whitespace, and removing
    extra spaces and punctuation before hashing.

    Parameters
    ----------
    hook:
        The hook text.
    body:
        The body text.
    cta:
        The CTA text.

    Returns
    -------
    str
        A 64-character hexadecimal SHA-256 digest.
    """
    parts: list[str] = []
    for text in [hook, body, cta]:
        normalized = text.lower().strip()
        # Collapse multiple whitespace into single space
        normalized = re.sub(r"\s+", " ", normalized)
        # Remove punctuation
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = normalized.strip()
        parts.append(normalized)

    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ===========================================================================
# 6. _check_similarity
# ===========================================================================


def _check_similarity(
    new_text: str,
    recent_texts: list[str],
    threshold: float = 0.85,
) -> bool:
    """Check if new_text is too similar to any recent text using TF-IDF + cosine similarity.

    Parameters
    ----------
    new_text:
        The new script text to compare.
    recent_texts:
        List of recent script texts to compare against.
    threshold:
        Cosine similarity threshold above which the script is considered
        too similar (default: 0.85).

    Returns
    -------
    bool
        ``True`` if the new text is too similar to any recent text.
    """
    if not recent_texts:
        return False

    try:
        all_texts = [new_text] + recent_texts

        vectorizer = TfidfVectorizer(
            lowercase=True,
            stop_words=None,  # keep stop-words for short text in Portuguese
            max_features=5000,
        )
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        # Compare the new text (index 0) against all recent texts (index 1+)
        similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])
        max_sim = float(similarities.max()) if similarities.size > 0 else 0.0

        is_similar = max_sim >= threshold

        logger.debug(
            "_check_similarity",
            max_similarity=round(max_sim, 4),
            threshold=threshold,
            is_similar=is_similar,
            compared_count=len(recent_texts),
        )

        return is_similar

    except ValueError:
        # e.g. empty vocabulary after tokenisation
        logger.debug("_check_similarity.empty_vocabulary")
        return False


# ===========================================================================
# 7. _call_llm
# ===========================================================================


def _call_llm(prompt: str) -> dict | None:
    """Call LLM API (OpenAI or Anthropic) to generate a script.

    Retries up to 3 times on failure. Parses the JSON response.

    Parameters
    ----------
    prompt:
        The full prompt text to send to the LLM.

    Returns
    -------
    dict or None
        Parsed JSON response dict, or ``None`` if all retries fail.
    """
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        try:
            if "claude" in settings.LLM_MODEL.lower() or "anthropic" in settings.LLM_MODEL.lower():
                response_text = _call_anthropic(prompt)
            else:
                response_text = _call_openai(prompt)

            result = _parse_json_response(response_text)
            if result is not None:
                logger.debug(
                    "_call_llm.success",
                    attempt=attempt,
                    model=settings.LLM_MODEL,
                )
                return result

            logger.warning(
                "_call_llm.parse_failed",
                attempt=attempt,
                response_preview=response_text[:300],
            )

        except Exception as exc:
            logger.warning(
                "_call_llm.attempt_failed",
                attempt=attempt,
                error=str(exc),
            )

    logger.error("_call_llm.all_attempts_failed", model=settings.LLM_MODEL)
    return None


def _call_openai(prompt: str) -> str:
    """Call OpenAI API for script generation."""
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        messages=[
            {
                "role": "system",
                "content": (
                    "Voce e um redator criativo especializado em roteiros curtos para "
                    "videos de afiliados em redes sociais brasileiras. "
                    "Responda APENAS com JSON valido, sem texto adicional."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content or ""


def _call_anthropic(prompt: str) -> str:
    """Call Anthropic API for script generation."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
        system=(
            "Voce e um redator criativo especializado em roteiros curtos para "
            "videos de afiliados em redes sociais brasileiras. "
            "Responda APENAS com JSON valido, sem texto adicional."
        ),
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text if response.content else ""


def _parse_json_response(text: str) -> dict | None:
    """Extract and parse JSON from LLM response text.

    Handles cases where the LLM wraps JSON in markdown code blocks.
    """
    text = text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object within the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None


# ===========================================================================
# Helper: load recent scripts for similarity comparison
# ===========================================================================


def _load_recent_scripts(session, platform: str) -> list[dict]:
    """Load recent scripts from the database for similarity comparison.

    Uses ``settings.RECENT_SCRIPTS_COMPARE`` to determine how many scripts
    to load (default: 10).

    Parameters
    ----------
    session:
        SQLAlchemy session.
    platform:
        Target platform to filter scripts.

    Returns
    -------
    list[dict]
        List of dicts with ``hook``, ``body``, ``cta`` keys.
    """
    try:
        cutoff = datetime.now(UTC) - timedelta(days=7)
        stmt = (
            select(Script.hook, Script.body, Script.cta)
            .where(
                Script.platform == platform,
                Script.created_at >= cutoff,
            )
            .order_by(Script.created_at.desc())
            .limit(settings.RECENT_SCRIPTS_COMPARE)
        )
        rows = session.execute(stmt).all()

        return [{"hook": row.hook, "body": row.body, "cta": row.cta} for row in rows]
    except Exception:
        logger.exception("_load_recent_scripts.error")
        return []
