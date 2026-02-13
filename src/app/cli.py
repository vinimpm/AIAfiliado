import click

from app.logging import setup_logging

setup_logging()


@click.group()
def cli():
    """AIAfiliado — CLI de gerenciamento."""
    pass


@cli.command()
def trigger_pipeline():
    """Dispara o pipeline diario manualmente."""
    from services.orchestrator import trigger_daily_pipeline

    click.echo("Disparando pipeline diario...")
    result = trigger_daily_pipeline.delay()
    click.echo(f"Task enviada: {result.id}")


@cli.command()
def check_health():
    """Verifica o account health score atual."""
    from datetime import date

    from services.account_health import evaluate_account_health

    click.echo("Avaliando account health...")
    result = evaluate_account_health.delay(str(date.today()))
    data = result.get(timeout=60)
    click.echo(f"Risk Score: {data['risk_score']}")
    click.echo(f"Risk Level: {data['risk_level']}")
    click.echo(f"Posts Allowed: {data['posts_allowed']}")
    click.echo(f"Cooldown: {data['cooldown_minutes']} min")


@cli.command()
def collect_trends():
    """Coleta tendencias manualmente."""
    from services.trend_engine import collect_trends

    click.echo("Coletando tendencias...")
    result = collect_trends.delay(run_id=0)
    data = result.get(timeout=300)
    click.echo(f"Tendencias encontradas: {len(data)}")
    for t in data[:5]:
        click.echo(f"  - {t['name']} (score: {t['score']})")


@cli.command()
@click.argument("script_id", type=int)
def generate_video(script_id: int):
    """Gera video para um script_id especifico."""
    from services.video_service import generate_video

    click.echo(f"Gerando video para script {script_id}...")
    result = generate_video.delay(script_id)
    data = result.get(timeout=1200)
    click.echo(f"Video gerado: {data['video_id']} - {data['s3_url']}")


@cli.command()
@click.argument("video_id", type=int)
@click.argument("platform", type=click.Choice(["tiktok", "instagram", "youtube"]))
def publish(video_id: int, platform: str):
    """Publica um video em uma plataforma."""
    from services.publish_service import schedule_publication

    click.echo(f"Publicando video {video_id} no {platform}...")
    result = schedule_publication.delay(video_id, platform)
    data = result.get(timeout=600)
    click.echo(f"Publicacao: {data['publication_id']} - Status: {data['status']}")


@cli.command()
def collect_metrics():
    """Coleta metricas de publicacoes recentes."""
    from services.analytics_service import collect_all_metrics

    click.echo("Coletando metricas...")
    result = collect_all_metrics.delay()
    result.get(timeout=300)
    click.echo("Metricas coletadas com sucesso.")


@cli.command()
def status():
    """Mostra status atual do sistema."""
    from sqlalchemy import func, select

    from models.daily_run import DailyRun
    from models.database import get_session_cm
    from models.product import Product
    from models.publication import Publication

    with get_session_cm() as session:
        last_run = session.execute(
            select(DailyRun).order_by(DailyRun.run_date.desc()).limit(1)
        ).scalar_one_or_none()

        active_products = session.execute(
            select(func.count()).where(Product.is_active.is_(True))
        ).scalar()

        total_posts = session.execute(
            select(func.count()).where(Publication.status == "POSTED")
        ).scalar()

        click.echo("=== AIAfiliado Status ===")
        if last_run:
            click.echo(f"Ultima execucao: {last_run.run_date}")
            click.echo(f"Risk Level: {last_run.risk_level}")
            click.echo(f"Status: {last_run.status}")
        else:
            click.echo("Nenhuma execucao registrada.")
        click.echo(f"Produtos ativos: {active_products}")
        click.echo(f"Publicacoes POSTED: {total_posts}")


if __name__ == "__main__":
    cli()
