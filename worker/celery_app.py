from celery import Celery
from services.logger import logger
from ollama import Client
from services.redis_client import get_redis_client
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

def get_queued_models():
    """Get models that have requests in the task queue"""
    queued_models = set()
    pass

@celery_app.task(
    bind=True, 
    acks_late=True,
    track_started=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_kwargs={'max_retries': 3},
    retry_backoff=True,
    retry_jitter=True)
def ollama_stream(self, query:str, channel_id:str, model_name:str):
    logger.info(f"Starting ollama_stream task for channel: {channel_id}")
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

        return {
            'status': 'completed',
            'channel_id': channel_id,
        }

    except Exception as e:
        logger.error(f"Error in ollama_stream for channel: {channel_id}: {str(e)}")
        try:
            redis_client.publish(channel_id, f'[ERROR: {str(e)}]')
            redis_client.publish(channel_id, '[DONE]')
        except:
            pass
        raise