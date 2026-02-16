"""Compliance Check Service -- validates scripts against regulatory blocklists,
platform CTA rules, duration constraints, and LLM-based content review.

Responsibilities:
    - Check text against hard (forbidden) and soft (warning) blocklists.
    - Validate CTA compatibility per platform rules.
    - Estimate script duration from word count.
    - Run LLM-based compliance review (OpenAI or Anthropic).
    - Orchestrate the full validation pipeline for a script.
"""

from __future__ import annotations

import json
from typing import Any

import openai

from app.celery_app import celery
from app.cloudwatch import emit_metric
from app.config import settings
from app.logging import get_logger

logger = get_logger(service="compliance")

# ---------------------------------------------------------------------------
# 1. Hard blocklist -- forbidden phrases (immediate rejection)
# ---------------------------------------------------------------------------

BLOCKLIST_HARD: list[str] = [
    # Medical claims
    "cura",
    "curar",
    "trata",
    "tratar",
    "previne",
    "prevenir",
    "diagnostico",
    "prescricao",
    "remedio",
    "medicamento",
    "aprovado pela anvisa",
    "clinicamente comprovado",
    # Financial claims
    "fique rico",
    "enriquecer",
    "renda garantida",
    "lucro garantido",
    "ganhe dinheiro facil",
    "esquema",
    "piramide",
    "retorno garantido",
    "investimento sem risco",
    # False scarcity
    "ultimas unidades",
    "so hoje",
    "vai acabar",
    "oferta por tempo limitado",
    # False endorsement
    "recomendado por medicos",
    "aprovado por dermatologistas",
    "celebridade usa",
    # Absolute guarantees
    "100% garantido",
    "resultado garantido",
    "funciona para todos",
    "nunca falha",
    "comprovado cientificamente",
]

# ---------------------------------------------------------------------------
# 2. Soft blocklist -- warning phrases (flagged but not auto-rejected)
# ---------------------------------------------------------------------------

BLOCKLIST_SOFT: list[str] = [
    "melhor do mercado",
    "numero 1",
    "mais vendido",
    "revolucionario",
    "incrivel",
    "milagroso",
    "voce precisa",
    "nao viva sem",
    "essencial",
]

# ---------------------------------------------------------------------------
# 3. CTA rules per platform
# ---------------------------------------------------------------------------

CTA_RULES: dict[str, dict[str, list[str]]] = {
    "tiktok": {
        "allowed": ["link na bio", "tiktok shop", "confira o produto"],
        "prohibited": ["http", "www", ".com"],
    },
    "instagram": {
        "allowed": ["link na bio", "confira nos stories"],
        "prohibited": ["http", "www", ".com"],
    },
    "youtube": {
        "allowed": ["link na descricao", "confira abaixo"],
        "prohibited": [],
    },
}

# ---------------------------------------------------------------------------
# LLM compliance prompt template
# ---------------------------------------------------------------------------

_COMPLIANCE_PROMPT = """Voce e um revisor de compliance para conteudo de marketing de afiliados
em redes sociais no Brasil. Analise o roteiro abaixo e verifique se ele viola
alguma das 8 regras listadas. Responda APENAS com JSON valido.

ROTEIRO:
- Plataforma: {platform}
- Hook: {hook}
- Body: {body}
- CTA: {cta}
- Caption: {caption}

REGRAS:
1. Nao fazer alegacoes medicas (curar, tratar, prevenir doencas).
2. Nao fazer promessas financeiras irreais (enriquecer, renda garantida).
3. Nao usar falsa escassez EXPLICITA (ex: "ultimas unidades", "so hoje", "vai acabar").
   NOTA: Frases comuns como "aproveita", "nao perde", "vale a pena" NAO sao falsa escassez.
4. Nao usar falso endosso (recomendado por medicos, celebridade usa).
5. Nao fazer garantias absolutas (100% garantido, funciona para todos).
6. Nao incluir links diretos em plataformas que proibem (TikTok, Instagram).
7. O CTA deve ser compativel com a plataforma.
8. Transparencia: a caption deve conter "#publi", "#ad" ou mencao de afiliado.
   Se a caption contiver "#publi" ou "#ad", esta regra esta CUMPRIDA.

Responda com o seguinte JSON:
{{
  "approved": true/false,
  "violations": ["descricao da violacao 1", ...],
  "severity": "none" | "low" | "medium" | "high",
  "suggestion": "sugestao de correcao se necessario"
}}
"""


# ===========================================================================
# 4. check_blocklist
# ===========================================================================


def check_blocklist(text: str) -> dict[str, Any]:
    """Check text against hard and soft blocklists.

    Parameters
    ----------
    text:
        The full text to scan (hook + body + cta + caption concatenated).

    Returns
    -------
    dict
        ``{"passed": bool, "hard_matches": [...], "soft_matches": [...]}``
        ``passed`` is ``False`` when any hard-blocklist phrase is found.
    """
    text_lower = text.lower().strip()

    hard_matches: list[str] = []
    for phrase in BLOCKLIST_HARD:
        if phrase in text_lower:
            hard_matches.append(phrase)

    soft_matches: list[str] = []
    for phrase in BLOCKLIST_SOFT:
        if phrase in text_lower:
            soft_matches.append(phrase)

    passed = len(hard_matches) == 0

    if hard_matches:
        logger.warning(
            "check_blocklist.hard_match",
            hard_matches=hard_matches,
            soft_matches=soft_matches,
        )
    elif soft_matches:
        logger.info(
            "check_blocklist.soft_match",
            soft_matches=soft_matches,
        )
    else:
        logger.debug("check_blocklist.clean")

    return {
        "passed": passed,
        "hard_matches": hard_matches,
        "soft_matches": soft_matches,
    }


# ===========================================================================
# 5. check_compliance_llm
# ===========================================================================


def check_compliance_llm(
    hook: str,
    body: str,
    cta: str,
    caption: str,
    platform: str,
) -> dict[str, Any]:
    """Run LLM-based compliance review on the script components.

    Calls OpenAI or Anthropic depending on ``settings.LLM_MODEL``.

    Returns
    -------
    dict
        ``{"approved": bool, "violations": [...], "severity": str, "suggestion": str}``
    """
    prompt = _COMPLIANCE_PROMPT.format(
        platform=platform,
        hook=hook,
        body=body,
        cta=cta,
        caption=caption or "(sem caption)",
    )

    default_result: dict[str, Any] = {
        "approved": False,
        "violations": ["erro na verificacao de compliance"],
        "severity": "high",
        "suggestion": "Revisar manualmente antes de publicar.",
    }

    try:
        if "claude" in settings.LLM_MODEL.lower() or "anthropic" in settings.LLM_MODEL.lower():
            response_text = _call_anthropic_compliance(prompt)
        else:
            response_text = _call_openai_compliance(prompt)

        # Parse JSON from the LLM response
        result = _parse_llm_json(response_text)

        if result is None:
            logger.error(
                "check_compliance_llm.parse_failed",
                response_text=response_text[:500],
            )
            return default_result

        # Validate expected keys
        approved = result.get("approved", False)
        violations = result.get("violations", [])
        severity = result.get("severity", "high")
        suggestion = result.get("suggestion", "")

        # Normalize severity
        if severity not in ("none", "low", "medium", "high"):
            severity = "high"

        logger.info(
            "check_compliance_llm.done",
            approved=approved,
            violations_count=len(violations),
            severity=severity,
        )

        return {
            "approved": bool(approved),
            "violations": violations,
            "severity": severity,
            "suggestion": suggestion,
        }

    except Exception as exc:
        logger.error(
            "check_compliance_llm.error",
            error=str(exc),
            exc_info=True,
        )
        return default_result


def _call_openai_compliance(prompt: str) -> str:
    """Call OpenAI API for compliance check."""
    client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        temperature=0.2,
        max_tokens=settings.LLM_MAX_TOKENS,
        messages=[
            {
                "role": "system",
                "content": (
                    "Voce e um revisor de compliance. "
                    "Responda APENAS com JSON valido, sem texto adicional."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content or ""


def _call_anthropic_compliance(prompt: str) -> str:
    """Call Anthropic API for compliance check."""
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=settings.LLM_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=0.2,
        system=(
            "Voce e um revisor de compliance. Responda APENAS com JSON valido, sem texto adicional."
        ),
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text if response.content else ""


def _parse_llm_json(text: str) -> dict | None:
    """Extract and parse JSON from LLM response text.

    Handles cases where the LLM wraps JSON in markdown code blocks.
    """
    text = text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
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
# 6. check_cta_compatibility
# ===========================================================================


def check_cta_compatibility(cta: str, platform: str) -> bool:
    """Check if a CTA is compatible with the given platform rules.

    Parameters
    ----------
    cta:
        The call-to-action text.
    platform:
        Target platform (``tiktok``, ``instagram``, ``youtube``).

    Returns
    -------
    bool
        ``True`` if the CTA passes platform rules, ``False`` otherwise.
    """
    cta_lower = cta.lower().strip()
    platform_lower = platform.lower().strip()

    rules = CTA_RULES.get(platform_lower)
    if rules is None:
        logger.warning(
            "check_cta_compatibility.unknown_platform",
            platform=platform,
        )
        # Unknown platform -- fail-safe: allow
        return True

    # Check prohibited terms
    for prohibited in rules["prohibited"]:
        if prohibited in cta_lower:
            logger.warning(
                "check_cta_compatibility.prohibited_found",
                platform=platform,
                cta=cta,
                prohibited_term=prohibited,
            )
            return False

    # Check if at least one allowed phrase is present
    has_allowed = any(allowed in cta_lower for allowed in rules["allowed"])
    if not has_allowed:
        logger.info(
            "check_cta_compatibility.no_allowed_match",
            platform=platform,
            cta=cta,
            allowed=rules["allowed"],
        )
        # Not having an explicitly allowed CTA is a warning, not a hard fail.
        # The LLM compliance check will provide further review.
        return True

    logger.debug(
        "check_cta_compatibility.passed",
        platform=platform,
        cta=cta,
    )
    return True


# ===========================================================================
# 7. check_duration
# ===========================================================================


def check_duration(hook: str, body: str, cta: str) -> bool:
    """Estimate script duration from word count and check if within range.

    Uses an average speaking rate of ~2.5 words per second (Brazilian
    Portuguese, natural pace).  Target duration is 20-45 seconds.

    Parameters
    ----------
    hook:
        The hook text.
    body:
        The body text.
    cta:
        The call-to-action text.

    Returns
    -------
    bool
        ``True`` if estimated duration is between 20 and 45 seconds.
    """
    full_text = " ".join(filter(None, [hook, body, cta]))
    word_count = len(full_text.split())
    estimated_seconds = word_count / 2.5

    in_range = 20.0 <= estimated_seconds <= 45.0

    logger.debug(
        "check_duration",
        word_count=word_count,
        estimated_seconds=round(estimated_seconds, 1),
        in_range=in_range,
    )

    return in_range


# ===========================================================================
# 8. validate_script -- full validation pipeline
# ===========================================================================


@celery.task(name="services.compliance.validate_script", bind=True, max_retries=2)
def validate_script(
    self,
    hook: str,
    body: str,
    cta: str,
    caption: str,
    platform: str,
) -> dict[str, Any]:
    """Run the full compliance validation pipeline on a script.

    Pipeline steps:
        1. Blocklist check -- hard matches cause immediate rejection.
        2. CTA compatibility check.
        3. Duration check.
        4. LLM-based compliance review.

    Parameters
    ----------
    hook:
        The hook text.
    body:
        The body text.
    cta:
        The call-to-action text.
    caption:
        The social media caption.
    platform:
        Target platform (``tiktok``, ``instagram``, ``youtube``).

    Returns
    -------
    dict
        ``{"approved": bool, "reason": str, "violations": [...], "severity": str}``
    """
    logger.info(
        "validate_script.start",
        platform=platform,
        hook_len=len(hook),
        body_len=len(body),
    )

    violations: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: Blocklist check
    # ------------------------------------------------------------------
    full_text = " ".join(filter(None, [hook, body, cta, caption]))
    blocklist_result = check_blocklist(full_text)

    if not blocklist_result["passed"]:
        reason = f"Frases proibidas encontradas: {', '.join(blocklist_result['hard_matches'])}"
        logger.warning(
            "validate_script.blocklist_rejected",
            hard_matches=blocklist_result["hard_matches"],
        )
        return {
            "approved": False,
            "reason": reason,
            "violations": blocklist_result["hard_matches"],
            "severity": "high",
        }

    # Track soft matches as low-severity violations
    if blocklist_result["soft_matches"]:
        violations.extend(
            [f"Alerta: uso de '{phrase}'" for phrase in blocklist_result["soft_matches"]]
        )

    # ------------------------------------------------------------------
    # Step 2: CTA compatibility
    # ------------------------------------------------------------------
    cta_ok = check_cta_compatibility(cta, platform)
    if not cta_ok:
        violations.append(f"CTA incompativel com a plataforma {platform}: '{cta}'")
        logger.warning(
            "validate_script.cta_incompatible",
            platform=platform,
            cta=cta,
        )
        return {
            "approved": False,
            "reason": f"CTA '{cta}' contem termos proibidos para {platform}.",
            "violations": violations,
            "severity": "medium",
        }

    # ------------------------------------------------------------------
    # Step 3: Duration check
    # ------------------------------------------------------------------
    duration_ok = check_duration(hook, body, cta)
    if not duration_ok:
        full_script = " ".join(filter(None, [hook, body, cta]))
        word_count = len(full_script.split())
        estimated = round(word_count / 2.5, 1)
        violations.append(f"Duracao estimada fora do intervalo (20-45s): ~{estimated}s")
        logger.info(
            "validate_script.duration_warning",
            estimated_seconds=estimated,
        )
        # Duration issues are a warning, not a hard rejection.
        # The script can still be approved if LLM compliance passes.

    # ------------------------------------------------------------------
    # Step 4: LLM compliance review
    # ------------------------------------------------------------------
    try:
        llm_result = check_compliance_llm(hook, body, cta, caption, platform)
    except Exception as exc:
        logger.error(
            "validate_script.llm_error",
            error=str(exc),
            exc_info=True,
        )
        # Fail-safe: reject when LLM review cannot be completed
        violations.append("Erro na verificacao de compliance via LLM.")
        return {
            "approved": False,
            "reason": "Falha na verificacao de compliance via LLM.",
            "violations": violations,
            "severity": "high",
        }

    # Merge LLM violations
    if llm_result.get("violations"):
        violations.extend(llm_result["violations"])

    severity = llm_result.get("severity", "none")
    approved = llm_result.get("approved", False)

    # If LLM approved but we have soft warnings, still approve with notes
    if approved and violations:
        reason = "Aprovado com alertas."
        # If only soft blocklist matches and duration warning, keep approved
        severity = "low" if severity == "none" else severity
    elif approved:
        reason = "Aprovado."
    else:
        reason = llm_result.get("suggestion", "Reprovado na revisao de compliance.")

    logger.info(
        "validate_script.done",
        approved=approved,
        severity=severity,
        violations_count=len(violations),
    )

    emit_metric("ComplianceRejectionRate", 0 if approved else 100, "Percent")

    return {
        "approved": approved,
        "reason": reason,
        "violations": violations,
        "severity": severity,
    }
