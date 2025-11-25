import redis
import redis.asyncio as aioredis
from services.logger import logger
from dotenv import load_dotenv
import os

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL", 'redis://localhost:6379/0')

def get_redis_client() -> redis.Redis:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()
        return redis_client

    except Exception as e:
        logger.exception(f"Error creating redis client: {str(e)}")
        raise

async def get_async_redis_client() -> aioredis.Redis:
    try:
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        return redis_client

    except Exception as e:
        logger.exception(f"Error creating async redis client: {str(e)}")
        raise

