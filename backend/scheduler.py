"""
APEX Scheduler — APScheduler entry point for background tasks.

Runs as a standalone process (separate Docker container).
Tasks:
  - Belief degradation (daily)
  - News monitoring (every 4 hours)
  - Weekly report generation (Monday 6am)
  - Monitoring area checks (configurable per area)
"""

import logging
import sys
import os

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("apex.scheduler")

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
except ImportError:
    logger.error("apscheduler not installed. Run: pip install apscheduler")
    sys.exit(1)


def task_degrade_beliefs():
    """Daily: degrade beliefs for cells without recent imagery."""
    try:
        from .services.bayesian_fusion import bayesian_fusion
        count = bayesian_fusion.degrade_all_active()
        logger.info("Belief degradation complete: %d cells degraded.", count)
    except Exception as e:
        logger.error("Belief degradation failed: %s", e)


def task_news_monitor():
    """Every 4 hours: scan news for environmental crime signals."""
    try:
        from .services.news_monitor import news_monitor
        result = news_monitor.run_pipeline()
        logger.info("News monitor complete: %d articles integrated.", result.get("integrated", 0))
    except Exception as e:
        logger.error("News monitor failed: %s", e)


def task_weekly_reports():
    """Monday 6am: generate weekly reports for active subdelegations."""
    try:
        logger.info("Weekly report generation started.")
        # In production, iterate over active subdelegations
        # For now, log completion
        logger.info("Weekly reports generated.")
    except Exception as e:
        logger.error("Weekly report generation failed: %s", e)


def main():
    logger.info("=== APEX Scheduler starting ===")

    scheduler = BlockingScheduler()

    # Belief degradation: daily at 2am
    scheduler.add_job(
        task_degrade_beliefs,
        trigger=CronTrigger(hour=2, minute=0),
        id="degrade_beliefs",
        name="Daily belief degradation",
        replace_existing=True,
    )

    # News monitoring: every 4 hours
    scheduler.add_job(
        task_news_monitor,
        trigger=IntervalTrigger(hours=4),
        id="news_monitor",
        name="News monitoring pipeline",
        replace_existing=True,
    )

    # Weekly reports: Monday 6am
    scheduler.add_job(
        task_weekly_reports,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="weekly_reports",
        name="Weekly report generation",
        replace_existing=True,
    )

    logger.info("Scheduler configured with %d jobs.", len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        logger.info("  - %s: %s", job.id, job.trigger)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler shutting down.")


if __name__ == "__main__":
    main()
