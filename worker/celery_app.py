from celery import Celery
from services.logger import logger
from ollama import Client
from services.redis_client import get_redis_client
from dotenv import load_dotenv
import os

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", 'redis://localhost:6379/0')
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
celery_app = Celery("streaming_ollama", broker=REDIS_URL, backend=None, include=['streaming_ollama'])

@celery_app.task()
def ollama_stream(query:str, channel_id:str):
    
    ollama_client = Client(host=OLLAMA_HOST)
    redis_client = get_redis_client() 

    model = 'qwen3:1.7b'
    messages = [
        {
            'role': 'user',
            'content': query,
        }
    ]
    for chunk in ollama_client.chat(model=model, messages=messages, stream=True):
        redis_client.publish(channel_id, chunk.message.content)
    redis_client.publish(channel_id, '[done]')