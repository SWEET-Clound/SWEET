import asyncio
import signal
from collections.abc import Awaitable, Callable
from functools import partial
from time import perf_counter

import structlog
from prometheus_client import Counter, Histogram, start_http_server

from memory_system.consolidation import Consolidator, HeuristicExtractor, HybridExtractor
from memory_system.dev import check_worker_database
from memory_system.embedding import EmbeddingClient, build_embedding_client
from memory_system.llm import (
    ModelResponseTooLarge,
    OpenAICompatibleExtractor,
    build_consolidation_extractor,
)
from memory_system.policy import MemoryPolicy
from memory_system.postgres import EventPayloadUnreadable, NotFoundError, PostgresRepository
from memory_system.runtime import configure_event_loop
from memory_system.settings import Settings
from memory_system.system import ConsolidationUnavailable, MemoryPolicyRejected, MemorySystem

_WORKER_JOBS = Counter(
    "memory_worker_jobs_total",
    "Background memory jobs processed",
    ("kind", "result"),
)
_DELETION_DURATION = Histogram(
    "memory_worker_deletion_duration_seconds",
    "Elapsed time of a worker deletion attempt, including the database claim",
    ("result",),
)
_CONSOLIDATION_DISCOVERY_DURATION = Histogram(
    "memory_worker_consolidation_discovery_duration_seconds",
    "Elapsed time of background consolidation candidate discovery",
    ("vector_matching",),
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
        await repository.fail_extraction(
            job.job_id, job.attempts, type(exc).__name__,
            terminal=isinstance(exc, ModelResponseTooLarge),
        )
        _WORKER_JOBS.labels(kind="extraction", result="failed").inc()
        return True
    await repository.complete_extraction(job.job_id, job.attempts)
    _WORKER_JOBS.labels(kind="extraction", result="completed").inc()
    return True


async def process_one_deletion(repository: PostgresRepository) -> bool:
    started = perf_counter()
    try:
        worked = await repository.process_one_deletion()
        if worked:
            _WORKER_JOBS.labels(kind="deletion", result="completed").inc()
            _DELETION_DURATION.labels(result="completed").observe(perf_counter() - started)
        return worked
    except Exception as exc:  # noqa: BLE001 - one deletion must not stop all background work
        _WORKER_JOBS.labels(kind="deletion", result="failed").inc()
        _DELETION_DURATION.labels(result="failed").observe(perf_counter() - started)
        structlog.get_logger().error(
            "memory_deletion_failed", error_code=type(exc).__name__
        )
        return False


async def process_extraction_iteration(
    repository: PostgresRepository, consolidator: Consolidator
) -> bool:
    try:
        return await process_one_extraction(repository, consolidator)
    except Exception as exc:  # noqa: BLE001 - a database outage must not kill the worker
        structlog.get_logger().error(
            "memory_extraction_iteration_failed", error_code=type(exc).__name__
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
    process_one: Callable[[], Awaitable[bool]],
    poll_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        worked = await process_one()
        if not worked:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass


async def backfill_embeddings(repository: PostgresRepository, client: EmbeddingClient) -> None:
    async with repository.embedding_backfill_lock(client.model, client.dimensions) as acquired:
        if not acquired:
            return
        items = await repository.pending_embeddings(client.model, client.dimensions)
        if not items:
            return
        vectors = await asyncio.to_thread(client.embed, [statement for _, statement in items])
        for (memory_id, statement), vector in zip(items, vectors, strict=True):
            await repository.store_embedding(memory_id, statement, client.model, vector)


async def process_background_consolidation(
    system: MemorySystem, embedding_client: EmbeddingClient | None = None,
) -> None:
    repository = system.repository
    async with repository.background_consolidation_lock() as acquired:
        if not acquired:
            return
        failures = 0
        embedding_options = {
            "embedding_model": embedding_client.model,
            "embedding_dimensions": embedding_client.dimensions,
        } if embedding_client is not None else {}
        with _CONSOLIDATION_DISCOVERY_DURATION.labels(
            vector_matching="enabled" if embedding_client is not None else "disabled"
        ).time():
            batches = await repository.discover_background_consolidation_batches(**embedding_options)
        for scope, ids in batches:
            if len(ids) < 2 or await repository.background_consolidation_completed(scope, ids):
                continue
            try:
                episodes = await system.propose_consolidation(scope, ids, background=True)
                procedures = await system.propose_consolidation(
                    scope, ids, kind="procedure", background=True
                )
                drafts = episodes[:2] + procedures[:1]
            except NotFoundError:
                continue
            except (ConsolidationUnavailable, EventPayloadUnreadable) as exc:
                _WORKER_JOBS.labels(kind="consolidation", result="failed").inc()
                structlog.get_logger().warning(
                    "memory_background_consolidation_batch_failed", error_code=type(exc).__name__
                )
                await repository.complete_background_consolidation(scope, ids, retry=True)
                failures += 1
                if failures >= 2:
                    return
                continue
            except MemoryPolicyRejected:
                drafts = []
            if await repository.complete_background_consolidation(scope, ids, drafts=drafts):
                _WORKER_JOBS.labels(kind="consolidation", result="completed").inc()
            return


async def maintenance_loop(
    repository: PostgresRepository,
    raw_event_retention_days: int,
    stop_event: asyncio.Event,
    embedding_client: EmbeddingClient | None = None,
    background_system: MemorySystem | None = None,
) -> None:
    log = structlog.get_logger()
    while not stop_event.is_set():
        try:
            await repository.run_maintenance(raw_event_retention_days)
        except Exception as exc:  # noqa: BLE001 - maintenance must not stop job processing
            log.error("memory_maintenance_failed", error_code=type(exc).__name__)
        if embedding_client is not None and not stop_event.is_set():
            try:
                await backfill_embeddings(repository, embedding_client)
            except Exception as exc:  # noqa: BLE001 - lexical recall remains available
                log.error("memory_embedding_backfill_failed", error_code=type(exc).__name__)
        if background_system is not None and not stop_event.is_set():
            try:
                await process_background_consolidation(background_system, embedding_client)
            except Exception as exc:  # noqa: BLE001 - retry on the next maintenance tick
                _WORKER_JOBS.labels(kind="consolidation", result="failed").inc()
                log.error("memory_background_consolidation_failed", error_code=type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except TimeoutError:
            pass


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    settings = Settings()  # type: ignore[call-arg]
    if settings.worker_database_url is None:
        raise ValueError("MEMORY_WORKER_DATABASE_URL is required for memory-worker")
    event_cipher = settings.build_event_cipher()
    database_url = settings.worker_database_url.get_secret_value()
    await asyncio.to_thread(check_worker_database, database_url)
    repository = PostgresRepository.from_url(
        database_url,
        event_cipher=event_cipher,
        rotate_event_ciphertext=settings.event_encryption_rotate,
    )
    consolidator = Consolidator(
        build_extractor(settings),
        MemoryPolicy(settings.working_memory_ttl_hours),
        write_status="quarantined" if settings.extraction_mode == "shadow" else "active",
    )
    log = structlog.get_logger()
    stop_event = stop_event or asyncio.Event()
    embedding_client = None
    background_model = None
    metrics_server = None
    try:
        embedding_client = build_embedding_client(settings)
        background_system = None
        background_model = build_consolidation_extractor(settings)
        if background_model is not None:
            background_system = MemorySystem(
                repository, MemoryPolicy(settings.working_memory_ttl_hours),
                settings.max_context_tokens, consolidation_extractor=background_model,
            )
        if settings.worker_metrics_port is not None:
            metrics_server, _ = start_http_server(
                settings.worker_metrics_port, addr=settings.worker_metrics_host
            )
        await asyncio.gather(
            maintenance_loop(
                repository, settings.raw_event_retention_days, stop_event, embedding_client,
                background_system,
            ),
            worker_loop(
                partial(process_one_deletion, repository),
                settings.worker_poll_seconds,
                stop_event,
            ),
            *[
                worker_loop(
                    partial(process_extraction_iteration, repository, consolidator),
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
            if metrics_server is not None:
                await asyncio.to_thread(metrics_server.shutdown)
                metrics_server.server_close()
            close_extractor = getattr(consolidator.extractor, "close", None)
            if close_extractor is not None:
                close_extractor()
        finally:
            if background_model is not None:
                background_model.close()
            if embedding_client is not None:
                embedding_client.http.close()
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

