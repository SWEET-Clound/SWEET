import asyncio
import hashlib
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import jwt
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import AwareDatetime
from sqlalchemy.exc import SQLAlchemyError

from memory_system.models import (
    AffectObservation,
    AuditEntry,
    BackgroundConsolidationConfirmation,
    BackgroundConsolidationConsent,
    BackgroundConsolidationDraftPage,
    CandidateDecisionAudit,
    CommandRequest,
    ConsolidationRequest,
    CorrectCommand,
    CorrectProcedureCommand,
    CorrectProcedureRequest,
    CorrectRequest,
    DeletionStatus,
    DoNotRememberCommand,
    DoNotRememberRequest,
    EventProcessingStatus,
    EventRequest,
    ForgetCommand,
    ForgetRequest,
    Identity,
    MemoryCandidate,
    MemoryLayer,
    MemoryListQuery,
    MemoryReviewReceipt,
    MemoryReviewRequest,
    MemorySummary,
    ProfileProjection,
    RecallDiagnostic,
    RecallRequest,
    RecallRequestBody,
    RememberCommand,
    RememberProcedureCommand,
    RememberProcedureRequest,
    RememberRequest,
    ThreadConsolidationRequest,
)
from memory_system.postgres import (
    EventPayloadUnreadable,
    IdempotencyConflictError,
    MemoryDeletionInProgress,
    MemorySourcesChanged,
    NotFoundError,
    RateLimitExceeded,
)
from memory_system.system import ConsolidationUnavailable, MemoryPolicyRejected, MemorySystem

_BEARER = HTTPBearer(auto_error=True)
_HTTP_REQUESTS = Counter(
    "memory_http_requests_total",
    "Memory API requests",
    ("method", "path", "status"),
)
_RATE_LIMITED_WRITES = Counter(
    "memory_rate_limited_writes_total",
    "Memory operations rejected by the per-scope rate limit",
    ("operation",),
)
_HTTP_DURATION = Histogram(
    "memory_http_request_duration_seconds",
    "Memory API handler latency including failed requests",
    ("path",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)


class JwtAuthenticator:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None,
        dev_secret: str | None = None,
        environment: str = "production",
    ) -> None:
        if environment == "production" and dev_secret is not None:
            raise ValueError("development JWT secret is forbidden in production")
        if not jwks_url and not dev_secret:
            raise ValueError("JWKS URL or development JWT secret is required")
        if environment == "production" and jwks_url:
            parsed = urlsplit(jwks_url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("production JWKS URL must use HTTPS")
        self.issuer = issuer
        self.audience = audience
        self.dev_secret = dev_secret
        self.jwks = (
            PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300) if jwks_url else None
        )

    async def __call__(
        self, credentials: Annotated[HTTPAuthorizationCredentials, Depends(_BEARER)]
    ) -> Identity:
        return await self.authenticate_token(credentials.credentials)

    async def authenticate_token(self, token: str) -> Identity:
        try:
            if self.dev_secret is not None:
                claims = jwt.decode(
                    token,
                    self.dev_secret,
                    algorithms=["HS256"],
                    issuer=self.issuer,
                    audience=self.audience,
                    options={"require": ["exp", "iss", "aud"]},
                )
            else:
                assert self.jwks is not None
                signing_key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256", "ES256"],
                    issuer=self.issuer,
                    audience=self.audience,
                    options={"require": ["exp", "iss", "aud"]},
                )
            raw_purposes = claims.get("memory_purposes", [])
            raw_memory_write = claims.get("memory_write", False)
            if not isinstance(raw_purposes, list) or not all(
                isinstance(value, str) for value in raw_purposes
            ):
                raise TypeError("memory_purposes must be a string list")
            if not isinstance(raw_memory_write, bool):
                raise TypeError("memory_write must be boolean")
            return Identity(
                tenant_id=claims["tenant_id"],
                user_id=claims["user_id"],
                agent_id=claims["agent_id"],
                allowed_purposes=set(raw_purposes),
                memory_write=raw_memory_write,
            )
        except PyJWKClientConnectionError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="jwks_unavailable",
            ) from exc
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc


def create_app(
    system: MemorySystem,
    authenticate: JwtAuthenticator,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]],
) -> FastAPI:
    app = FastAPI(title="Persistent Memory", version="1", lifespan=lifespan)

    def require_memory_writer(
        identity: Annotated[Identity, Depends(authenticate)],
    ) -> Identity:
        if not identity.memory_write:
            raise HTTPException(status_code=403, detail="memory_write_not_allowed")
        return identity

    @app.middleware("http")
    async def diagnostics_header(request: Request, call_next):
        started = perf_counter()
        try:
            response = await call_next(request)
        finally:
            route = request.scope.get("route")
            path = route.path if route is not None else "unmatched"
            _HTTP_DURATION.labels(path=path).observe(perf_counter() - started)
        _HTTP_REQUESTS.labels(
            method=request.method if request.method in {
                "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"
            } else "OTHER",
            path=path,
            status=str(response.status_code),
        ).inc()
        if "X-Memory-Diagnostics-Id" not in response.headers:
            response.headers["X-Memory-Diagnostics-Id"] = str(uuid4())
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": error["loc"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(_: Request, __: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "idempotency_conflict"})

    @app.exception_handler(MemoryDeletionInProgress)
    async def memory_deletion_in_progress(_: Request, __: MemoryDeletionInProgress) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "memory_deletion_in_progress"})

    @app.exception_handler(MemorySourcesChanged)
    async def memory_sources_changed(_: Request, __: MemorySourcesChanged) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": "memory_sources_changed"})

    @app.exception_handler(NotFoundError)
    async def hidden_not_found(_: Request, __: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"code": "scope_invalid_or_forbidden"})

    @app.exception_handler(MemoryPolicyRejected)
    async def policy_rejected(_: Request, exc: MemoryPolicyRejected) -> JSONResponse:
        return JSONResponse(status_code=422, content={"code": exc.reason})

    @app.exception_handler(ConsolidationUnavailable)
    async def consolidation_unavailable(_: Request, __: ConsolidationUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "consolidation_unavailable"})

    @app.exception_handler(RateLimitExceeded)
    async def write_rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        _RATE_LIMITED_WRITES.labels(operation=exc.operation).inc()
        return JSONResponse(
            status_code=429,
            content={
                "code": "model_rate_limited" if exc.operation == "consolidation"
                else "write_rate_limited",
                "operation": exc.operation,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(EventPayloadUnreadable)
    async def database_unavailable(
        _: Request, __: SQLAlchemyError | EventPayloadUnreadable
    ) -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": "memory_unavailable"})

    @app.post("/v1/memory/events")
    async def record_event(
        body: EventRequest,
        identity: Annotated[Identity, Depends(require_memory_writer)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)],
    ) -> JSONResponse:
        receipt = await system.record_event(body.to_domain(identity, idempotency_key))
        return JSONResponse(
            status_code=200 if receipt.duplicate else 201,
            content=receipt.model_dump(mode="json"),
        )

    @app.post("/v1/memory/context:build")
    async def build_context(
        body: RecallRequestBody,
        identity: Annotated[Identity, Depends(authenticate)],
        response: Response,
    ):
        if body.purpose not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        context = await system.build_context(
            RecallRequest(
                **identity.scope().model_dump(),
                thread_id=body.thread_id,
                query=body.query,
                purpose=body.purpose,
                token_budget=body.token_budget,
                now=body.request_time,
            )
        )
        response.headers["X-Memory-Diagnostics-Id"] = context.diagnostics_id
        return context

    @app.get("/v1/memory/consolidations/background", response_model=BackgroundConsolidationDraftPage)
    async def list_background_consolidation_drafts(
        identity: Annotated[Identity, Depends(authenticate)],
        cursor: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.repository.list_background_consolidation_drafts(
            identity.scope(), cursor=cursor or "", limit=limit
        )

    @app.delete("/v1/memory/consolidations/background/{source_digest}", status_code=204)
    async def dismiss_background_consolidation_drafts(
        source_digest: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
        identity: Annotated[Identity, Depends(authenticate)],
    ) -> None:
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        await system.repository.dismiss_background_consolidation_drafts(
            identity.scope(), source_digest
        )

    @app.post("/v1/memory/consolidations/background/{source_digest}/confirm")
    async def confirm_background_consolidation(
        source_digest: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
        body: BackgroundConsolidationConfirmation,
        identity: Annotated[Identity, Depends(require_memory_writer)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
        ],
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        draft = body.draft
        fields = {
            "scope": identity.scope(), "statement": draft.statement,
            "source_event_id": body.source_event_id, "supporting_event_ids": draft.source_event_ids,
        }
        if draft.proposed_layer is MemoryLayer.PROCEDURAL:
            command = RememberProcedureCommand(action="remember_procedure", **fields)
        elif draft.proposed_layer is MemoryLayer.EPISODIC and draft.expires_at is not None:
            command = RememberCommand(action="remember", expires_at=draft.expires_at, **fields)
        else:
            raise HTTPException(status_code=422, detail="invalid_background_draft")
        payload_hash = hashlib.sha256(
            f"background:{source_digest}:{body.model_dump_json()}".encode()
        ).hexdigest()
        return await system.execute_command_idempotent(
            command, idempotency_key, payload_hash,
            background_draft=(source_digest, body.ordinal, draft),
        )

    @app.put("/v1/memory/consolidations/background", status_code=204)
    async def set_background_consolidation(
        body: BackgroundConsolidationConsent,
        identity: Annotated[Identity, Depends(authenticate)],
    ) -> None:
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        if body.enabled and not identity.memory_write:
            raise HTTPException(status_code=403, detail="memory_write_not_allowed")
        await system.repository.set_background_consolidation(identity.scope(), body.enabled)

    @app.get("/v1/memory/consolidations/threads", response_model=list[str])
    async def discover_consolidation_threads(
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.repository.discover_consolidation_threads(identity.scope())

    @app.get("/v1/memory/consolidations/sources", response_model=list[UUID])
    async def discover_consolidation_sources(
        thread_id: Annotated[str, Query(min_length=1, max_length=128)],
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.repository.discover_consolidation_sources(identity.scope(), thread_id)

    @app.post("/v1/memory/consolidations:preview", response_model=list[MemoryCandidate])
    async def propose_consolidation(
        body: ConsolidationRequest | ThreadConsolidationRequest,
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        scope = identity.scope()
        if isinstance(body, ThreadConsolidationRequest):
            ids = await system.repository.discover_consolidation_sources(scope, body.thread_id)
            if len(ids) < 2:
                return []
        else:
            ids = [str(event_id) for event_id in body.event_ids]
        if len(set(ids)) != len(ids):
            raise HTTPException(status_code=422, detail="distinct_events_required")
        return await system.propose_consolidation(
            scope, ids, kind=body.kind,
            automatic_sources=isinstance(body, ThreadConsolidationRequest),
        )

    @app.get("/v1/memory/profile", response_model=ProfileProjection)
    async def build_profile(
        identity: Annotated[Identity, Depends(authenticate)],
        response: Response,
        purpose: Annotated[
            Literal["conversation", "task", "history", "support"], Query()
        ] = "conversation",
        token_budget: Annotated[int, Query(ge=0, le=512)] = 512,
        request_time: AwareDatetime | None = None,
    ):
        if purpose not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        projection = await system.build_profile(
            identity.scope(),
            purpose,
            token_budget,
            request_time or datetime.now(UTC),
        )
        response.headers["X-Memory-Diagnostics-Id"] = projection.diagnostics_id
        return projection

    @app.get("/v1/memory/events/{event_id}", response_model=EventProcessingStatus)
    async def event_processing_status(
        event_id: str,
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        return await system.repository.event_processing_status(identity.scope(), event_id)

    @app.post("/v1/memory/commands")
    async def execute_command(
        body: CommandRequest,
        identity: Annotated[Identity, Depends(require_memory_writer)],
        idempotency_key: Annotated[
            str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
        ],
    ):
        scope = identity.scope()
        if isinstance(body, RememberRequest):
            command = RememberCommand(scope=scope, **body.model_dump())
        elif isinstance(body, RememberProcedureRequest):
            if "review" not in identity.allowed_purposes:
                raise HTTPException(status_code=403, detail="purpose_not_allowed")
            command = RememberProcedureCommand(scope=scope, **body.model_dump())
        elif isinstance(body, CorrectRequest):
            command = CorrectCommand(scope=scope, **body.model_dump())
        elif isinstance(body, CorrectProcedureRequest):
            if "review" not in identity.allowed_purposes:
                raise HTTPException(status_code=403, detail="purpose_not_allowed")
            command = CorrectProcedureCommand(scope=scope, **body.model_dump())
        elif isinstance(body, ForgetRequest):
            command = ForgetCommand(scope=scope, **body.model_dump())
        elif isinstance(body, DoNotRememberRequest):
            command = DoNotRememberCommand(scope=scope, **body.model_dump())
        else:
            raise HTTPException(status_code=422, detail="unsupported_command")
        excluded = {"scope"}
        if isinstance(command, RememberCommand | RememberProcedureCommand) and not command.supporting_event_ids:
            excluded.add("supporting_event_ids")
        if isinstance(command, RememberCommand) and command.expires_at is None:
            excluded.add("expires_at")
        payload_hash = hashlib.sha256(command.model_dump_json(exclude=excluded).encode()).hexdigest()
        receipt = await system.execute_command_idempotent(
            command, idempotency_key, payload_hash
        )
        return JSONResponse(
            status_code=202 if receipt.status == "deletion_pending" else 200,
            content=receipt.model_dump(mode="json"),
        )

    @app.get("/v1/memory/items", response_model=list[MemorySummary])
    async def list_memories(
        identity: Annotated[Identity, Depends(authenticate)],
        response: Response,
        layers: Annotated[list[MemoryLayer] | None, Query()] = None,
        statuses: Annotated[list[str] | None, Query()] = None,
        cursor: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        if {"quarantined", "rejected"}.intersection(statuses or ()) and (
            "review" not in identity.allowed_purposes
        ):
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        page = list(
            await system.list_memories(
                MemoryListQuery(
                    scope=identity.scope(),
                    layers=layers or [],
                    statuses=statuses or ["active"],
                    cursor=str(cursor) if cursor else None,
                    limit=limit + 1,
                )
            )
        )
        items = page[:limit]
        if len(page) > limit:
            response.headers["X-Next-Cursor"] = items[-1].memory_id
        if identity.allowed_purposes.isdisjoint({"support", "history"}):
            return [item.model_copy(update={"affect": None}) for item in items]
        return items

    @app.get("/v1/memory/deletions/{job_id}", response_model=DeletionStatus)
    async def deletion_status(
        job_id: str,
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        return await system.repository.deletion_status(identity.scope(), job_id)

    @app.post(
        "/v1/memory/items/{memory_id}:review", response_model=MemoryReviewReceipt
    )
    async def review_memory(
        memory_id: UUID,
        body: MemoryReviewRequest,
        identity: Annotated[Identity, Depends(require_memory_writer)],
    ):
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.review_memory(
            identity.scope(), str(memory_id), body.decision
        )

    @app.put("/v1/memory/items/{memory_id}/conflicts/{other_id}/coexistence", status_code=204)
    async def resolve_memory_conflict_as_coexisting(
        memory_id: UUID,
        other_id: UUID,
        identity: Annotated[Identity, Depends(require_memory_writer)],
    ) -> None:
        if "review" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        await system.repository.resolve_memory_conflict_as_coexisting(
            identity.scope(), str(memory_id), str(other_id)
        )

    @app.get(
        "/v1/memory/items/{memory_id}/affect-history", response_model=list[AffectObservation]
    )
    async def affect_history(
        memory_id: UUID,
        identity: Annotated[Identity, Depends(authenticate)],
        purpose: Literal["history", "support"] = "history",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        as_of: AwareDatetime | None = None,
    ):
        if purpose not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.affect_history(identity.scope(), str(memory_id), limit, as_of)

    @app.get("/v1/memory/audit", response_model=list[AuditEntry])
    async def list_audit(
        identity: Annotated[Identity, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        if "audit" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.list_audit(identity.scope(), limit)

    @app.get(
        "/v1/memory/candidate-decisions",
        response_model=list[CandidateDecisionAudit],
    )
    async def list_candidate_decisions(
        identity: Annotated[Identity, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        if "audit" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.list_candidate_decisions(identity.scope(), limit)

    @app.get(
        "/v1/memory/diagnostics/{diagnostics_id}",
        response_model=RecallDiagnostic,
    )
    async def get_recall_diagnostic(
        diagnostics_id: UUID,
        identity: Annotated[Identity, Depends(authenticate)],
    ):
        if "audit" not in identity.allowed_purposes:
            raise HTTPException(status_code=403, detail="purpose_not_allowed")
        return await system.get_recall_diagnostic(identity.scope(), str(diagnostics_id))

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        await system.repository.ping()
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def main() -> None:
    config = uvicorn.Config(
        "memory_system.bootstrap:create_application",
        factory=True,
        host="0.0.0.0",
        port=8080,
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        server.run()

