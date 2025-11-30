from celery import Celery
from services.logger import logger
from ollama import Client
from services.redis_client import get_redis_client
from utils.manage_models import load_or_queue_model, cleanup_inactive_model_tracking
from services.cache import mark_model_active, mark_model_inactive, mark_model_queued, mark_model_dequeued
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
)

# Need to create these tasks:
# 1. Model Loading with VRAM Management
# 2. Stream Ollama response to Redis Pub/Sub
# 3. Orchestrator of task both streaming and loading
# 4. Periodic cleanup

@celery_app.task(
    bind=True,
    acks_late=True,
    track_started=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    retry_jitter=True
)
def manage_model_loading(self, model_name:str, gpu_index: int=0):
    """
    Task to handle model loading with VRAM Management
    1. Check if the model is loaded
    2. Checks available vram
    3. unloads idle model if required
    4. Loads the requested model
    """
    logger.info(f"Task {self.request.id}: Managing model loading for {model_name}")

    try:
        self.update_state(state='LOADING_MODEL', meta={'model_name': model_name})
        result = load_or_queue_model(model_name, gpu_index)
        if result['status'] == 'loaded':
            logger.info(f"Task {self.request.id}: Successfully loaded model {model_name}")
            return result
        elif result['status'] == 'insufficient_vram':
            logger.warning(f"Task {self.request.id}: Insufficient VRAM for model {model_name}")
            return result
        else: 
            logger.error(f"Task {self.request.id}: Failed to load model {model_name}")
            return result
        
    except Exception as e:
        logger.exception(f"Task {self.request.id}: Error in manage model loading: {str(e)}")
        return {"status": "error", "message": str(e)}

@celery_app.task(
    bind=True, 
    acks_late=True,
    track_started=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    retry_jitter=True)
def ollama_stream(self, query:str, channel_id:str, model_name:str):
    """
    Task to stream ollama responses to Redis Pub/Sub
    1. Mark the model as active
    2. Stream responses
    3. Mark model as inactive
    """

    logger.info(f"Task {self.request.id}: Starting ollama_stream for channel: {channel_id}")
    mark_model_active(model_name, self.request.id)
    mark_model_dequeued(model_name, self.request.id)
    try:
        ollama_client = Client(host=OLLAMA_HOST)
        redis_client = get_redis_client() 
        messages = [
            {
                'role': 'user',
                'content': query,
            }
        ]
        self.update_state(state='STREAMING', meta={'channel_id':channel_id})
        for chunk in ollama_client.chat(model=model_name, messages=messages, stream=True):
            content = chunk.message.content
            redis_client.publish(channel_id, content)
        redis_client.publish(channel_id, '[DONE]')

        logger.info(f"Task {self.request.id}: Completed streaming for channel: {channel_id}")

        return {
            'status': 'completed',
            'channel_id': channel_id,
            'model_name': model_name
        }

    except Exception as e:
        logger.error(f"Error in ollama_stream for channel: {channel_id}: {str(e)}")
        try:
            redis_client = get_redis_client()
            redis_client.publish(channel_id, f'[ERROR: {str(e)}]')
            redis_client.publish(channel_id, '[DONE]')
        except:
            pass
        raise
    finally:
        mark_model_inactive(model_name, self.request.id)
        logger.info(f"Task {self.request.id}: Marked model {model_name} as inactive")

# FIXME: Use proprer chaining not synchornous method calls
@celery_app.task(bind=True)
def process_ollama_request(self, query: str, model_name: str, channel_id: str, gpu_index: int=0):
    """
    Orchestrator task that handles both model loading and streaming
    1. Ensure that the model is loaded
    2. Streams the response
    """
    logger.info(f"Task {self.request.id}: Processing Ollama Request for model: {model_name}")
    try:
        redis_client = get_redis_client()
        self.update_state(state="LOADING_MODEL", meta={'model_name': model_name, 'channel_id': channel_id})
        loading_result = manage_model_loading(model_name, gpu_index)

        if loading_result['status'] != 'loaded':
            error_msg = loading_result.get('message', 'Failed to load model')
            logger.error(f"Task {self.request.id}: {error_msg}")
            redis_client.publish(channel_id, f'[ERROR: {error_msg}]')
            redis_client.publish(channel_id, '[DONE]')
            return {
                'status': 'failed',
                'reason': 'model loading failure',
                'details': loading_result
            }
        
        logger.info(f"Task {self.request.id}: Model {model_name} loaded successfully")

        self.update_state(state='STREAMING', meta={'model_name': model_name, 'channel_id': channel_id})
        streaming_result = ollama_stream(query=query, channel_id=channel_id, model_name=model_name)
        mark_model_queued(model_name, ollama_stream.id)

        return {
            'status': 'completed',
            'loading_result': loading_result,
            'streaming_result': streaming_result
        }

    except Exception as e:
        logger.exception(f"Task {self.request.id}: Error in process_ollama_request: {str(e)}")
        try:
            redis_client = get_redis_client()
            redis_client.publish(channel_id, f'[ERROR: {str(e)}]')
            redis_client.publish(channel_id, '[DONE]')
        except:
            pass
        return {
            'status': 'error',
            'message': str(e)
        }
    
@celery_app.task(bind=True)
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