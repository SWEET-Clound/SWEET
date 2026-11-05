import asyncio
import signal

import structlog
from prometheus_client import Counter

from memory_system.consolidation import Consolidator, HeuristicExtractor, HybridExtractor
from memory_system.dev import check_worker_database
from memory_system.llm import OpenAICompatibleExtractor
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.runtime import configure_event_loop
from memory_system.settings import Settings

_WORKER_JOBS = Counter(
    "memory_worker_jobs_total",
    "Background memory jobs processed",
    ("kind", "result"),
)


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
        _WORKER_JOBS.labels(kind="extraction", result="failed").inc()
        return True
    await repository.complete_extraction(job.job_id)
    _WORKER_JOBS.labels(kind="extraction", result="completed").inc()
    return True


async def process_one_deletion(repository: PostgresRepository) -> bool:
    try:
        worked = await repository.process_one_deletion()
        if worked:
            _WORKER_JOBS.labels(kind="deletion", result="completed").inc()
        return worked
    except Exception as exc:  # noqa: BLE001 - one deletion must not stop all background work
        _WORKER_JOBS.labels(kind="deletion", result="failed").inc()
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
    heuristic = HeuristicExtractor(settings.working_memory_ttl_hours)
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
        timeout_seconds=settings.model_timeout_seconds,
        max_attempts=settings.model_max_attempts,
    )
    if settings.extraction_mode in {"shadow", "model"}:
        return model
    return HybridExtractor(heuristic, model)


async def worker_loop(
    repository: PostgresRepository,
    consolidator: Consolidator,
    poll_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        worked = await process_worker_iteration(repository, consolidator)
        if not worked:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass


async def maintenance_loop(
    repository: PostgresRepository,
    raw_event_retention_days: int,
    stop_event: asyncio.Event,
) -> None:
    log = structlog.get_logger()
    while not stop_event.is_set():
        try:
            await repository.run_maintenance(raw_event_retention_days)
        except Exception as exc:  # noqa: BLE001 - maintenance must not stop job processing
            log.error("memory_maintenance_failed", error_code=type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            pass


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    settings = Settings()  # type: ignore[call-arg]
    if settings.worker_database_url is None:
        raise ValueError("MEMORY_WORKER_DATABASE_URL is required for memory-worker")
    database_url = settings.worker_database_url.get_secret_value()
    await asyncio.to_thread(check_worker_database, database_url)
    repository = PostgresRepository.from_url(database_url)
    consolidator = Consolidator(
        build_extractor(settings),
        MemoryPolicy(),
        write_status="quarantined" if settings.extraction_mode == "shadow" else "active",
    )
    log = structlog.get_logger()
    stop_event = stop_event or asyncio.Event()
    try:
        await asyncio.gather(
            maintenance_loop(
                repository, settings.raw_event_retention_days, stop_event
            ),
            *[
                worker_loop(
                    repository,
                    consolidator,
                    settings.worker_poll_seconds,
                    stop_event,
                )
                for _ in range(settings.worker_concurrency)
            ]
        )
    except asyncio.CancelledError:
        log.info("worker_stopped")
        raise
    finally:
        try:
            close_extractor = getattr(consolidator.extractor, "close", None)
            if close_extractor is not None:
                close_extractor()
        finally:
            await repository.close()


async def _run_worker_with_signals() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    loop_handlers: set[signal.Signals] = set()

    for signum in previous_handlers:
        try:
            loop.add_signal_handler(signum, stop_event.set)
            loop_handlers.add(signum)
        except (NotImplementedError, RuntimeError):
            signal.signal(
                signum,
                lambda *_: loop.call_soon_threadsafe(stop_event.set),
            )

    try:
        await run_worker(stop_event)
    finally:
        for signum, previous in previous_handlers.items():
            if signum in loop_handlers:
                loop.remove_signal_handler(signum)
            signal.signal(signum, previous)


def main() -> None:
    configure_event_loop()
    asyncio.run(_run_worker_with_signals())

