"""Payment webhook endpoints."""

import json

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from fastapi_app.api.deps import RedisClient
from fastapi_app.models.access import WebhookPayload, WebhookResponse

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Redis keys for webhook processing
IDEMPOTENCY_PREFIX = "memora:webhook:"
RETRY_QUEUE_KEY = "memora:webhook:retry_queue"
IDEMPOTENCY_TTL = 86400  # 24 hours


async def process_payment_webhook(payload: WebhookPayload, redis: RedisClient) -> None:
    """
    Background processing of payment webhook.

    Per CONTEXT.md:
    - Creates MariaDB subscription record (via Frappe API - stubbed for now)
    - Adds grants to Redis (idempotent via SADD)
    - Transaction log for failure recovery
    """
    try:
        # 1. Get grant components from Product Grant
        # TODO: Implement Frappe API call to get grant keys from product_grant_id
        # For now, use product_grant_id as the grant key directly
        grant_keys = [payload.product_grant_id]

        # 2. Create MariaDB subscription record
        # TODO: Implement Frappe API call to create Memora Player Subscription
        # This is stubbed - actual implementation depends on Frappe whitelisted method
        logger.info(
            "subscription_creation_stub",
            player_id=payload.player_id,
            product_grant_id=payload.product_grant_id,
            transaction_id=payload.transaction_id,
        )

        # 3. Add grants to Redis (idempotent via SADD)
        access_key = f"memora:access:{payload.player_id}"
        if grant_keys:
            await redis.sadd(access_key, *grant_keys)

        # 4. Mark as completed
        idempotency_key = f"{IDEMPOTENCY_PREFIX}{payload.event_id}"
        await redis.set(idempotency_key, "completed", ex=IDEMPOTENCY_TTL)

        logger.info(
            "webhook_processed",
            event_id=payload.event_id,
            player_id=payload.player_id,
            grants=grant_keys,
        )

    except Exception as e:
        logger.error(
            "webhook_processing_failed",
            event_id=payload.event_id,
            error=str(e),
        )
        # Queue for retry
        await queue_for_retry(payload, redis)


async def queue_for_retry(payload: WebhookPayload, redis: RedisClient) -> None:
    """Add failed webhook to retry queue.

    Per CONTEXT.md: Transaction log for failure recovery.
    """
    await redis.lpush(RETRY_QUEUE_KEY, json.dumps(payload.model_dump()))
    logger.info(
        "webhook_queued_for_retry",
        event_id=payload.event_id,
    )


@router.post("/payment", response_model=WebhookResponse)
async def payment_webhook(
    payload: WebhookPayload,
    redis: RedisClient,
    background_tasks: BackgroundTasks,
) -> WebhookResponse:
    """
    Handle payment completion webhook.

    Per CONTEXT.md:
    - Provider-agnostic interface
    - Idempotent via event_id tracking
    - Fast acknowledgment, background processing

    Returns quickly with "accepted" status, processes in background.
    """
    logger.info(
        "webhook_received",
        event_id=payload.event_id,
        event_type=payload.event_type,
        transaction_id=payload.transaction_id,
        player_id=payload.player_id,
    )

    # Check idempotency
    idempotency_key = f"{IDEMPOTENCY_PREFIX}{payload.event_id}"
    existing = await redis.get(idempotency_key)

    if existing:
        status_value = existing.decode() if isinstance(existing, bytes) else existing
        if status_value == "completed":
            return WebhookResponse(status="already_processed", message="Event already completed")
        elif status_value == "processing":
            return WebhookResponse(status="already_processed", message="Event currently processing")

    # Mark as processing
    await redis.set(idempotency_key, "processing", ex=IDEMPOTENCY_TTL)

    # Process in background for fast acknowledgment
    background_tasks.add_task(process_payment_webhook, payload, redis)

    return WebhookResponse(status="accepted", message="Webhook received and queued for processing")


async def process_retry_queue(redis: RedisClient, max_items: int = 10) -> int:
    """
    Process items from retry queue.

    Called by Frappe scheduled task (to be implemented in Phase 7).

    Returns:
        Number of items processed
    """
    processed = 0

    for _ in range(max_items):
        item = await redis.rpop(RETRY_QUEUE_KEY)
        if not item:
            break

        try:
            data = item.decode() if isinstance(item, bytes) else item
            payload = WebhookPayload(**json.loads(data))
            await process_payment_webhook(payload, redis)
            processed += 1

        except Exception as e:
            # Re-queue on failure
            await redis.lpush(RETRY_QUEUE_KEY, item)
            logger.error("retry_processing_failed", error=str(e))
            break  # Stop on failure to prevent infinite loop

    return processed
