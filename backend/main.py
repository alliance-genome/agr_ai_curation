"""Main FastAPI application for AI Curation Platform Backend."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import sys
import os

# Disable telemetry before any imports that might use it
os.environ['POSTHOG_DISABLED'] = 'true'  # Disable PostHog telemetry
os.environ['ANONYMIZED_TELEMETRY'] = 'False'  # Disable ChromaDB telemetry (capital F)

from src.api import documents, chunks, processing, strategies, settings, schema, health, chat, pdf_viewer, feedback, auth, users, agent_studio, logs, flows, files, maintenance, batch
from src.api.admin import connections_router as admin_connections_router
from src.api.admin import prompts_router as admin_prompts_router
from src.config import get_pdf_storage_path
from src.lib.weaviate_client.connection import WeaviateConnection, set_connection
from src.lib.weaviate_client.settings import get_embedding_config
from src.models.sql.database import SessionLocal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# Validate DOCLING_TIMEOUT at startup
_docling_timeout = os.getenv('DOCLING_TIMEOUT', '30')
try:
    _docling_timeout_int = int(_docling_timeout)
    _min_timeout = 300  # 5 minutes minimum
    if _docling_timeout_int < _min_timeout:
        error_msg = (
            f"❌ CRITICAL: DOCLING_TIMEOUT is set to {_docling_timeout_int} seconds, "
            f"but must be at least {_min_timeout} seconds (5 minutes).\n"
            f"   PDF processing can take several minutes, especially for complex documents.\n"
            f"   Please update .env file: DOCLING_TIMEOUT=300"
        )
        print(error_msg, flush=True)
        logger.error(error_msg)
        sys.exit(1)  # Prevent startup with insufficient timeout
    else:
        logger.info(f"✅ DOCLING_TIMEOUT validated: {_docling_timeout_int} seconds")
except ValueError:
    error_msg = f"❌ CRITICAL: DOCLING_TIMEOUT must be an integer, got: {_docling_timeout}"
    print(error_msg, flush=True)
    logger.error(error_msg)
    sys.exit(1)


async def initialize_weaviate_collections(connection: WeaviateConnection):
    """Create required Weaviate collections with multi-tenancy enabled.

    Idempotent initialization:
    - If collections don't exist: Create with multi-tenancy enabled
    - If collections exist without multi-tenancy: Drop and recreate (one-time migration)
    - If collections exist with multi-tenancy: Skip (preserve tenant data)
    """
    from weaviate.classes.config import Configure, Property, DataType

    # Get the configured embedding model from settings (it's sync, not async)
    from src.lib.weaviate_client.settings import _current_config
    embedding_model = _current_config["embedding"]["modelName"]
    logger.info(f"Using embedding model from settings: {embedding_model}")

    with connection.session() as client:
        # Get list of existing collections - list_all() returns strings in v4
        collections = client.collections.list_all()
        # In Weaviate v4, list_all() returns collection names as strings directly
        existing_names = collections if isinstance(collections, list) else [c for c in collections]

        # Define required collections with multi-tenancy enabled
        required_collections = {
            "DocumentChunk": {
                # Use text2vec-openai for server-side embeddings
                "vectorizer_config": Configure.Vectorizer.text2vec_openai(
                    model=embedding_model,  # Use model from settings
                    vectorize_collection_name=False
                ),
                "vector_index_config": Configure.VectorIndex.hnsw(),
                "multi_tenancy_config": Configure.multi_tenancy(enabled=True),  # Enable multi-tenancy
                "properties": [
                    Property(name="documentId", data_type=DataType.TEXT),
                    Property(name="chunkIndex", data_type=DataType.INT),
                    Property(name="content", data_type=DataType.TEXT, vectorize_property_name=True),  # Vectorize content
                    Property(name="contentPreview", data_type=DataType.TEXT, vectorize_property_name=False),  # Don't vectorize preview
                    Property(name="elementType", data_type=DataType.TEXT),
                    Property(name="pageNumber", data_type=DataType.INT),
                    Property(name="sectionTitle", data_type=DataType.TEXT),
                    Property(name="sectionPath", data_type=DataType.TEXT_ARRAY),
                    Property(name="contentType", data_type=DataType.TEXT),
                    Property(name="metadata", data_type=DataType.TEXT),
                    Property(name="embeddingTimestamp", data_type=DataType.DATE),
                    Property(name="docItemProvenance", data_type=DataType.TEXT),  # For chunk highlighting
                ]
            },
            "PDFDocument": {
                "vectorizer_config": Configure.Vectorizer.none(),
                "multi_tenancy_config": Configure.multi_tenancy(enabled=True),  # Enable multi-tenancy
                "properties": [
                    Property(name="filename", data_type=DataType.TEXT),
                    Property(name="fileSize", data_type=DataType.INT),
                    Property(name="uploadDate", data_type=DataType.DATE),
                    Property(name="creationDate", data_type=DataType.DATE),
                    Property(name="lastAccessedDate", data_type=DataType.DATE),
                    Property(name="processingStatus", data_type=DataType.TEXT),
                    Property(name="embeddingStatus", data_type=DataType.TEXT),
                    Property(name="chunkCount", data_type=DataType.INT),
                    Property(name="vectorCount", data_type=DataType.INT),
                    Property(name="metadata", data_type=DataType.TEXT),
                ]
            }
        }

        # Check each collection and handle appropriately
        for collection_name, config in required_collections.items():
            if collection_name not in existing_names:
                # Collection doesn't exist - create with multi-tenancy
                logger.info(f"Creating collection with multi-tenancy: {collection_name}")
                client.collections.create(name=collection_name, **config)
                logger.info(f"✅ Collection {collection_name} created with multi-tenancy enabled")
            else:
                # Collection exists - check if multi-tenancy is enabled
                collection = client.collections.get(collection_name)
                collection_config = collection.config.get()

                # Check if multi-tenancy is already enabled
                if collection_config.multi_tenancy_config and collection_config.multi_tenancy_config.enabled:
                    logger.info(f"✅ Collection {collection_name} already has multi-tenancy enabled - skipping")
                else:
                    # Multi-tenancy not enabled - need to migrate (one-time operation)
                    logger.warning(f"⚠️  Collection {collection_name} exists without multi-tenancy - performing one-time migration")
                    logger.warning(f"⚠️  This will DELETE all existing data in {collection_name}")
                    client.collections.delete(collection_name)
                    client.collections.create(name=collection_name, **config)
                    logger.info(f"✅ Collection {collection_name} recreated with multi-tenancy enabled")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    logger.info("Starting up Weaviate Control Panel API...")

    try:
        # Get Weaviate connection details from environment
        weaviate_host = os.getenv("WEAVIATE_HOST", "localhost")
        weaviate_port = os.getenv("WEAVIATE_PORT", "8080")
        weaviate_scheme = os.getenv("WEAVIATE_SCHEME", "http")
        weaviate_url = f"{weaviate_scheme}://{weaviate_host}:{weaviate_port}"

        logger.info(f"Connecting to Weaviate at {weaviate_url}...")
        connection = WeaviateConnection(url=weaviate_url)
        await connection.connect_to_weaviate()
        logger.info("✅ Successfully connected to Weaviate")

        # Simple health check - try to list collections
        try:
            with connection.session() as client:
                collections = client.collections.list_all()
                logger.info(f"✅ Weaviate health check passed - found {len(collections)} collections")
        except Exception as health_error:
            logger.error(f"❌ WEAVIATE HEALTH CHECK FAILED: {health_error}")
            logger.error("Cannot start without Weaviate connection!")
            raise RuntimeError("Weaviate is not accessible - check if container is running") from health_error

        # Set the global connection for other modules to use
        set_connection(connection)

        # Initialize required collections
        await initialize_weaviate_collections(connection)
        logger.info("✅ Successfully initialized Weaviate collections")

        # Sync prompts from YAML to database (YAML is source of truth)
        # This must run BEFORE cache initialization
        from src.lib.config.prompt_loader import load_prompts
        from src.lib.prompts.cache import initialize as init_prompt_cache
        db = SessionLocal()
        try:
            # Load prompts from YAML files into database
            counts = load_prompts(db=db)
            if counts.get("skipped"):
                logger.debug("Prompt loader already initialized")
            else:
                logger.info(
                    f"✅ Prompts synced from YAML: {counts['base_prompts']} base, "
                    f"{counts['group_rules']} group rules"
                )

            # Initialize prompt cache from database
            init_prompt_cache(db)
            logger.info("✅ Prompt cache initialized")

            # Load group definitions from config/groups.yaml
            # This must run after prompts so group rules can be resolved
            from src.lib.config.groups_loader import load_groups
            groups = load_groups()
            logger.info(f"✅ Group definitions loaded: {len(groups)} groups")
        except Exception as e:
            logger.error(f"❌ FATAL: Failed to initialize prompts/groups: {e}")
            db.rollback()  # Rollback any partial changes on failure
            raise  # Re-raise to prevent app startup
        finally:
            db.close()

        # Load connection definitions from config/connections.yaml
        # This enables health checking and connection status tracking
        try:
            from src.lib.config.connections_loader import (
                load_connections,
                check_required_services_healthy,
                get_required_connections,
                get_optional_connections,
            )
            connections = load_connections()
            logger.info(f"✅ Connection definitions loaded: {len(connections)} services")

            # Check health of required services
            required_services = get_required_connections()
            optional_services = get_optional_connections()
            logger.info(f"   Required services: {[s.service_id for s in required_services]}")
            logger.info(f"   Optional services: {[s.service_id for s in optional_services]}")

            # HEALTH_CHECK_STRICT_MODE controls whether startup blocks on required service failures
            # Default: True (enforce required services are healthy)
            # Set to "false" for development/testing with partial infrastructure
            strict_mode = os.environ.get("HEALTH_CHECK_STRICT_MODE", "true").lower() != "false"

            if strict_mode and required_services:
                logger.info("🔍 Checking required service health (HEALTH_CHECK_STRICT_MODE=true)...")
                all_healthy, failed_services = await check_required_services_healthy()

                if not all_healthy:
                    error_msg = f"Required services are unhealthy: {failed_services}"
                    logger.error(f"❌ FATAL: {error_msg}")
                    logger.error("Set HEALTH_CHECK_STRICT_MODE=false to bypass (not recommended for production)")
                    raise RuntimeError(error_msg)

                logger.info("✅ All required services are healthy")
            elif required_services:
                logger.warning("⚠️ HEALTH_CHECK_STRICT_MODE=false - skipping required service health enforcement")
                logger.warning("   This is not recommended for production deployments")

        except FileNotFoundError as e:
            logger.warning(f"⚠️ Connections config not found (optional): {e}")
        except RuntimeError:
            raise  # Re-raise startup failures
        except Exception as e:
            logger.warning(f"⚠️ Failed to load connections config (non-fatal): {e}")

        # Initialize Langfuse observability
        try:
            from src.lib.openai_agents.langfuse_client import initialize_langfuse, is_langfuse_configured
            if is_langfuse_configured():
                langfuse_client = initialize_langfuse()
                if langfuse_client:
                    logger.info("✅ Langfuse observability initialized")
                else:
                    logger.warning("⚠️ Langfuse initialization returned None - tracing may not work")
            else:
                logger.info("ℹ️ Langfuse not configured - running without observability")
        except ImportError as e:
            logger.warning(f"⚠️ Langfuse package not available: {e}")
        except Exception as e:
            logger.warning(f"⚠️ Langfuse initialization failed (non-fatal): {e}")

    except Exception as e:
        logger.error(f"❌ CRITICAL: Failed to initialize Weaviate: {e}")
        logger.error("The application cannot start without Weaviate database connection!")
        raise  # Fail fast - don't start if DB isn't ready

    yield

    logger.info("Shutting down Weaviate Control Panel API...")
    try:
        connection = WeaviateConnection()
        await connection.close()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


app = FastAPI(
    title="AI Curation Platform API",
    description="Unified API for AI Chat (OpenAI Agents SDK) and Weaviate Control Panel",
    version="2.0.0",
    lifespan=lifespan
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Convert Pydantic 422 validation errors to 400 with ErrorResponse shape.

    This ensures the feedback API contract matches the specification, which expects
    400 with status/error/details fields, not FastAPI's default 422 response.

    Only applies to /api/feedback endpoints - other endpoints still get 422.
    """
    # Only apply custom error format to feedback endpoints
    if request.url.path.startswith("/api/feedback"):
        # Extract validation errors from Pydantic
        details = []
        for error in exc.errors():
            field = ".".join(str(loc) for loc in error["loc"] if loc != "body")
            message = error["msg"]
            details.append({"field": field, "message": message})

        # Return 400 with contract-compliant ErrorResponse shape
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "Validation error",
                "details": details,
            },
        )

    # For other endpoints, use FastAPI's default 422 response
    # Sanitize errors to ensure JSON serialization (ctx may contain ValueError objects)
    sanitized_errors = []
    for error in exc.errors():
        sanitized_error = {
            "type": error.get("type"),
            "loc": error.get("loc"),
            "msg": error.get("msg"),
        }
        # Convert ctx.error to string if present (avoid ValueError serialization issues)
        if "ctx" in error and error["ctx"]:
            ctx = error["ctx"]
            if "error" in ctx and isinstance(ctx["error"], Exception):
                sanitized_error["ctx"] = {"error": str(ctx["error"])}
            else:
                sanitized_error["ctx"] = ctx
        sanitized_errors.append(sanitized_error)

    return JSONResponse(
        status_code=422,
        content={"detail": sanitized_errors},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
# Authentication endpoints (under /auth)
app.include_router(auth.router, tags=["Authentication"])

# User profile endpoints (under /users)
app.include_router(users.router, tags=["Users"])

# Chat API endpoints (under /api) - OpenAI Agents SDK
app.include_router(chat.router, tags=["Chat"])

# Feedback API endpoints (under /api/feedback)
app.include_router(feedback.router, tags=["Feedback"])

# Maintenance message API endpoint (under /api/maintenance)
app.include_router(maintenance.router, tags=["Maintenance"])

# Agent Studio API endpoints (under /api/agent-studio)
app.include_router(agent_studio.router, tags=["Agent Studio"])

# Flow CRUD API endpoints (under /api/flows)
app.include_router(flows.router, tags=["Flows"])

# Batch processing API endpoints (under /api/batches)
app.include_router(batch.router, tags=["Batches"])
# Flow validation for batch compatibility (under /api/flows/{id}/validate-batch)
app.include_router(batch.flow_validation_router, tags=["Batches"])

# File output API endpoints (under /api/files)
app.include_router(files.router, tags=["Files"])

# Weaviate Control Panel endpoints (already have /weaviate prefix in router definitions)
app.include_router(documents.router, tags=["Documents"])
app.include_router(chunks.router, tags=["Chunks"])
app.include_router(processing.router, tags=["Processing"])
app.include_router(strategies.router, tags=["Strategies"])
app.include_router(settings.router, tags=["Settings"])
app.include_router(schema.router, tags=["Schema"])
app.include_router(health.router, tags=["Health"])
app.include_router(pdf_viewer.router, tags=["PDF Viewer"])
app.include_router(logs.router, prefix="/api", tags=["Logs"])

# Admin endpoints (privileged operations - requires ADMIN_EMAILS allowlist)
app.include_router(admin_prompts_router, tags=["Admin - Prompts"])
app.include_router(admin_connections_router, tags=["Admin - Health"])

# Static mount for original PDF storage
pdf_storage_path = get_pdf_storage_path()
pdf_storage_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=pdf_storage_path), name="uploads")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "AI Curation Platform API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

@app.get("/health")
async def health_check():
    """Comprehensive health check endpoint."""
    health_status = {
        "status": "healthy",
        "services": {
            "app": "running",
            "openai_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        }
    }

    # Check Weaviate connectivity
    try:
        from src.lib.weaviate_client.connection import get_connection
        connection = get_connection()
        # Actually test the connection
        with connection.session() as client:
            client.collections.list_all()
            health_status["services"]["weaviate"] = "connected"
    except Exception as e:
        health_status["services"]["weaviate"] = "disconnected"
        health_status["status"] = "degraded"

    # Check curation database connectivity
    try:
        from src.lib.database.agr_client import get_agr_db_client
        # Test database connection by querying for a single gene
        db_client = get_agr_db_client()
        # Simple query to verify connection (limit 1 for speed)
        db_client.get_genes_by_taxon('NCBITaxon:6239', limit=1)
        health_status["services"]["curation_db"] = "connected"
    except Exception as e:
        logger.error(f"Curation database health check failed: {e}")
        health_status["services"]["curation_db"] = "disconnected"
        health_status["status"] = "degraded"

    # Check Redis connectivity (used for cross-worker stream cancellation)
    try:
        from src.lib.redis_client import get_redis
        redis_client = await get_redis()
        await redis_client.ping()
        health_status["services"]["redis"] = "connected"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        health_status["services"]["redis"] = "disconnected"
        health_status["status"] = "degraded"

    return health_status
