import redis
import redis.asyncio as aioredis
from services.logger import logger
from dotenv import load_dotenv
import os
import time
from uuid import uuid4

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


class RedisLock:
    def __init__(self, name:str, ttl:int=10):
        self.name = f"lock:{name}"
        self.ttl = ttl
        self.value = str(uuid4().hex)
        self.client = get_redis_client()

    def acquire(self, block:bool=True, timeout:int=10):
        end_time = time.time() + timeout
        while True:
            if self.client.set(self.name, self.value, nx=True, ex=self.ttl):
                return True
            if not block or time.time() > end_time:
                return False
            time.sleep(0.05)

    def release(self):
        pipe = self.client.pipeline()
        while True:
            try:
                pipe.watch(self.name)
                val = pipe.get(self.name)
                if val is not None and val.decode() == self.value:
                    pipe.multi()
                    pipe.delete(self.name)
                    pipe.execute()
                else:
                    pipe.unwatch()

                break

            except Exception:
                pipe.unwatch()
                break
