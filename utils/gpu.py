import pynvml
import ollama
from ollama import Client
from services.logger import logger

def get_vram_usage(gpu_index:int=0):
    try:
        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()

        if gpu_index>= device_count:
            logger.error(f"Invalid gpu index: {gpu_index}")
            pynvml.nvmlShutdown()
            return None
        
        device = pynvml.nvmlDeviceGetHandleByIndex(gpu_index)
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(device)

        total_vram = mem_info.total / (1024**2)
        used_vram = mem_info.used / (1024**2)
        free_vram = mem_info.free / (1024**2)

        pynvml.nvmlShutdown()

        return {
            "total": total_vram,
            "used": used_vram,
            "free": free_vram
        }
    
    except pynvml.NVMLError as e:
        logger.error(f"Error accessing NVML: {str(e)}")
    except Exception as e:
        logger.exception(f"Error in getting vram usage: {str(e)}")


def get_model_size(client: ollama.Client, model_name:str):
    try:
        model_info = client.show(model=model_name)

        if 'size' in model_info:
            model_size_bytes = model_info.get('size')
            model_size_mib = model_size_bytes / (1024**2)
            return model_size_mib
        
        logger.warning(f"No size field in model")
        return None
    
    except Exception as e:
        logger.exception(f"Error in getting model info: {str(e)}")

def load_or_queue_model(model_name:str, gpu_index:int=0):
    """
    There could be 3 cases for model loading:
    1. load the model if there is enough free vram
    2. else queue the client request
    3. kick the ideal loaded model to make space
    4. [Optionally] forcefully route the request to a warm model
    """
    try:
        ollama_client = Client()
        gpu_index = 0
        model_size = get_model_size(ollama_client, model_name)
        vram_usage = get_vram_usage(gpu_index)

        if model_size> vram_usage['free']:
            logger.info(f"Queued user request:\nmodel size: {model_size}\navailable memory: {vram_usage['free']}")
            loaded_models = ollama_client.ps()
        elif model_size < vram_usage['free']:
            logger.info(f"Loaded new model: {model_name}")

    except Exception as e:
        logger.exception()

