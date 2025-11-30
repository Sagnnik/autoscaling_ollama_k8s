from services.redis_client import get_redis_client
from services.logger import logger

def mark_model_active(model_name:str, task_id: str):
    """Mark a model thats actively processing a request"""
    redis_client = get_redis_client()
    redis_client.sadd(f"active_model:{model_name}", task_id)
    logger.info(f"Marked model {model_name} as active for task {task_id}")

def mark_model_inactive(model_name:str, task_id:str):
    """Remove a task from the active model set"""
    redis_client = get_redis_client()
    redis_client.srem(f"active_model:{model_name}", task_id)
    logger.info(f"Marked model {model_name} inactive for task {task_id}")

def get_active_models():
    r = get_redis_client()
    active = set()

    for key in r.scan_iter('active_model:*'):
        if r.scard(key) > 0:
            model_name = key.replace('active_model:', '')
            active.add(model_name)

    logger.info(f"Active Models: {active}")
    return active

def mark_model_queued(model_name:str, task_id:str):
    r = get_redis_client()
    r.sadd(f"queued_model:{model_name}", task_id)
    logger.info(f"Marked model {model_name} as QUEUED for task {task_id}")

def mark_model_dequeued(model_name: str, task_id: str):
    r = get_redis_client()
    r.srem(f"queued_model:{model_name}", task_id)
    logger.info(f"Marked model {model_name} as DEQUEUED for task {task_id}")

def get_queued_models():
    r = get_redis_client()
    queued = set()

    for key in r.scan_iter("queued_model:*"):
        if r.scard(key)>0:
            model_name = key.replace("queued_model:", "")
            queued.add(model_name)

    logger.info(f"Queued Models: {queued}")
    return queued