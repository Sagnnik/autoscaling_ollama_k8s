from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from services.logger import logger
from ollama import Client
from services.redis_client import get_redis_client
from utils.manage_models import load_or_queue_model, cleanup_inactive_model_tracking
from services.cache import mark_model_active, mark_model_inactive, mark_model_reserved, mark_model_unreserved
from dotenv import load_dotenv
import os

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", 'redis://localhost:6379/0')
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

celery_app = Celery("streaming_ollama", broker=REDIS_URL, backend=REDIS_URL)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    worker_send_task_events=True,
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    task_soft_time_limit=300,  # 5 min
    task_time_limit=360,  # 6 min
    result_expires=3600,  # 1 hour
    timezone='UTC',
    enable_utc=True,
)

def stream_ollama_to_redis(query:str, channel_id:str, model_name:str, task_id:str):
    """
    Task to stream ollama responses to Redis Pub/Sub
    1. Mark the model as active
    2. Stream responses
    3. Mark model as inactive
    """

    logger.info(f"Task {task_id}: Starting ollama stream for channel: {channel_id}")
    r = get_redis_client()
    o_client = Client(host=OLLAMA_HOST)

    mark_model_active(model_name, task_id)
    mark_model_unreserved(model_name, task_id)

    try:
        messages = [{"role": "user", "content": query}]

        for chunk in o_client.chat(model=model_name, messages=messages, stream=True):
            content = chunk.message.content
            if content:
                r.publish(channel_id, content)

        r.publish(channel_id, '[DONE]')
        logger.info(f"Task {task_id}: Completed Streaming for channel: {channel_id}")

        return {
            "status": "completed",
            "channel_id": channel_id,
            "model_name": model_name
        }
    except Exception as e:
        logger.error(f"Task {task_id}: Error in ollama stream for channel {channel_id}: {str(e)}")
        try:
            r.publish(channel_id, f"[ERROR: {str(e)}]")
            r.publish(channel_id, "[DONE]")
        except Exception:
            pass
        raise

    finally:
        mark_model_inactive(model_name, task_id)
        logger.info(f"Task {task_id}: Marked model {model_name} as inactive")

@celery_app.task(bind=True, acks_late=True, track_started=True, max_retries=20)
def process_ollama_request(self, query:str, model_name:str, channel_id:str, gpu_index:int=0):
    """
    Orchestrator task that handles both model loading and streaming
    1. Ensure that the model is loaded
    2. If loaded stream the response
    3. Else queue the process
    """
    task_id = self.request.id
    r = get_redis_client()
    try:
        self.update_state(state="MANAGING_MODEL", meta={"model_name": model_name, "channel_id": channel_id})

        loading_result = load_or_queue_model(model_name=model_name, gpu_index=gpu_index) 
        status = loading_result.get('status')
        
        if status == 'loaded':
            self.update_state(state="STREAMING", meta={"model_name": model_name, "channel_id": channel_id})
            mark_model_reserved(model_name, task_id) 
            streaming_result = stream_ollama_to_redis(query=query, model_name=model_name, channel_id=channel_id, task_id=task_id)
            return streaming_result
        
        elif status == "insufficient_vram":
            logger.info(f"Task {task_id}: Insufficient VRAM, queuing model '{model_name}' and retrying later")
            mark_model_reserved(model_name, task_id) 
            raise self.retry(countdown=5)
        
        elif status == "error": 
            msg = loading_result.get("message", "Error while preparing model")
            logger.error(f"Task {task_id}: {msg}")
            r.publish(channel_id, f"[ERROR: {msg}]")
            r.publish(channel_id, "[DONE]")
            return {"status": "error", "reason": msg, "loading_result": loading_result}
        
        msg = f"Unhandled model loading status: {status!r}"
        logger.error(f"Task {task_id}: {msg}")
        r.publish(channel_id, f"[ERROR: {msg}]")
        r.publish(channel_id, "[DONE]")
        return {"status": "failed", "reason": msg, "loading_result": loading_result}
    
    except MaxRetriesExceededError as e:
        msg = f"Max retries exceeded while waiting for VRAM for model '{model_name}'"
        logger.error(f"Task {task_id}: {msg}")
        try:
            r.publish(channel_id, f"[ERROR: {msg}]")
            r.publish(channel_id, "[DONE]")
        except Exception:
            pass
        mark_model_unreserved(model_name, task_id)
        return {"status": "error", "message": msg}
    
    except Exception as e:
        logger.exception(f"Task {task_id}: Unexpected error in process_ollama_request: {e}")
        try:
            r.publish(channel_id, f"[ERROR: {e}]")
            r.publish(channel_id, "[DONE]")
        except Exception:
            pass
        return {"status": "error", "message": str(e)}
        
@celery_app.task(bind=True, name="cleanup_stale_model_tracking")
def cleanup_stale_model_tracking(self):
    """Periodic task to clean up stale model tracking entries in Redis"""

    logger.info(f"Task {self.request.id}: Running cleanup stale model tracking")
    try:
        cleanup_inactive_model_tracking()
        return {
            'status': 'completed',
            'message': 'Cleanup Successful'
        }
    except Exception as e:
        logger.exception(f"Task {self.request.id}: Error in cleanup: {str(e)}")
        return {'status': 'error', 'message': str(e)}
    
# Beat Schedule
celery_app.conf.beat_schedule = {
    "cleanup-model-tracking-keys-every-5-min": {
        "task": "cleanup_stale_model_tracking",
        "schedule": 300.0,
    }
}
