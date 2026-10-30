import asyncio

import structlog

from memory_system.consolidation import Consolidator, HeuristicExtractor, HybridExtractor
from memory_system.llm import OpenAICompatibleExtractor
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.runtime import configure_event_loop
from memory_system.settings import Settings


async def process_one_extraction(
    repository: PostgresRepository, consolidator: Consolidator
) -> bool:
    job = await repository.claim_next_extraction()
    if job is None:
        return False
    try:
        await consolidator.process(repository, job)
    except Exception as exc:  # noqa: BLE001 - persist job failure and keep the worker alive
        await repository.fail_extraction(job.job_id, type(exc).__name__)
        return True
    await repository.complete_extraction(job.job_id)
    return True


async def process_one_deletion(repository: PostgresRepository) -> bool:
    try:
        return await repository.process_one_deletion()
    except Exception as exc:  # noqa: BLE001 - one deletion must not stop all background work
        structlog.get_logger().error(
            "memory_deletion_failed", error_code=type(exc).__name__
        )
        return False


async def process_worker_iteration(
    repository: PostgresRepository, consolidator: Consolidator
) -> bool:
    try:
        worked = await process_one_extraction(repository, consolidator)
        return await process_one_deletion(repository) or worked
    except Exception as exc:  # noqa: BLE001 - a database outage must not kill the worker
        structlog.get_logger().error(
            "memory_worker_iteration_failed", error_code=type(exc).__name__
        )
        return False


def build_extractor(settings: Settings):
    heuristic = HeuristicExtractor()
    if settings.extraction_mode == "heuristic":
        return heuristic
    model = OpenAICompatibleExtractor(
        base_url=settings.model_base_url or "",
        model=settings.extraction_model or "",
        api_key=(
            settings.model_api_key.get_secret_value()
            if settings.model_api_key is not None
            else None
        ),
    )
    if settings.extraction_mode in {"shadow", "model"}:
        return model
    return HybridExtractor(heuristic, model)


async def worker_loop(
    repository: PostgresRepository,
    consolidator: Consolidator,
    poll_seconds: float,
) -> None:
    while True:
        worked = await process_worker_iteration(repository, consolidator)
        if not worked:
            await asyncio.sleep(poll_seconds)


async def maintenance_loop(
    repository: PostgresRepository,
    raw_event_retention_days: int,
) -> None:
    log = structlog.get_logger()
    while True:
        try:
            await repository.run_maintenance(raw_event_retention_days)
        except Exception as exc:  # noqa: BLE001 - maintenance must not stop job processing
            log.error("memory_maintenance_failed", error_code=type(exc).__name__)
        await asyncio.sleep(60)


async def run_worker() -> None:
    settings = Settings()  # type: ignore[call-arg]
    database_url = settings.worker_database_url or settings.database_url
    repository = PostgresRepository.from_url(database_url.get_secret_value())
    consolidator = Consolidator(
        build_extractor(settings),
        MemoryPolicy(),
        write_status="quarantined" if settings.extraction_mode == "shadow" else "active",
    )
    log = structlog.get_logger()
    try:
        await asyncio.gather(
            maintenance_loop(repository, settings.raw_event_retention_days),
            *[
                worker_loop(repository, consolidator, settings.worker_poll_seconds)
                for _ in range(settings.worker_concurrency)
            ]
        )
    except asyncio.CancelledError:
        log.info("worker_stopped")
        raise
    finally:
        await repository.close()


def main() -> None:
    configure_event_loop()
    asyncio.run(run_worker())

