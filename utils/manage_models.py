import pynvml
import ollama
from ollama import Client
from services.logger import logger
from services.redis_client import get_redis_client
from services.cache import get_active_models, get_queued_models
import os
from dotenv import load_dotenv

load_dotenv()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

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
        pulled_models = client.list()
        for model in pulled_models.get('models', []):
            if model['model'] == model_name:
                model_size = model.get('size')
                model_size_mib = model_size / (1024**2)
                return model_size_mib
            
        logger.warning(f"No size field in model")
        return None
    
    except Exception as e:
        logger.exception(f"Error in getting model info: {str(e)}")



def load_or_queue_model(model_name:str, gpu_index:int=0):
    """
    Smart model loading with VRAM management

    1. If the model size is more than total vram, throw error
    2. Load the model if there is enough free vram
    3. If there isn't enough free vram then 
        - see if there are inactive models for unloading
        - see there isn't same model request in the task queue already
        - if same models are in queue add task to queue
        - unload in smallest models first
        - queue the request if unable to free enough vram

    Returns:
    dict: status information about the model loading operation
    """
    """
    ollama ps output: 
    vars(model_info['models'][i]) ->
    {'model': 'qwen3:1.7b',
    'name': 'qwen3:1.7b',
    'digest': '8f68893c685c3ddff2aa3fffce2aa60a30bb2da65ca488b61fff134a4d1730e7',
    'expires_at': datetime.datetime(2025, 11, 29, 9, 42, 54, 368810, tzinfo=TzInfo(0)),
    'size': 1898590336,
    'size_vram': 1898590336,
    'details': ModelDetails(parent_model='', format='gguf', family='qwen3', families=['qwen3'], parameter_size='2.0B', quantization_level='Q4_K_M'),
    'context_length': 4096}
    """
    try:
        ollama_client = Client(host=OLLAMA_HOST)
        model_size = get_model_size(ollama_client, model_name)
        vram_usage = get_vram_usage(gpu_index)

        if not model_size or not vram_usage:
            logger.error("Failed to get model size or VRAM usage")
            return {"status": "error", "message": "Unable to retrieve system information"}

        # Check if model size exceeds total VRAM
        if model_size > vram_usage['total']:
            error_msg = f"Model size ({model_size:.2f} MiB) exceeds total VRAM ({vram_usage['total']:.2f} MiB)"
            logger.error(error_msg)
            return {"status": "error", "message": error_msg}

        loaded_models = ollama_client.ps()
        
        # Check if model is already loaded
        for model in loaded_models.get('models', []):
            if model.get('name') == model_name:
                logger.info(f"Model {model_name} is already loaded")
                return {"status": "loaded", "message": "Model already loaded", "model": model_name}

        # Case 1: Enough free VRAM - load the model directly
        if model_size < vram_usage['free']:
            logger.info(f"Loading model {model_name} - sufficient free VRAM ({vram_usage['free']:.2f} MiB)")
            ollama_client.generate(model=model_name, prompt="", keep_alive=-1)
            return {"status": "loaded", "message": "Model loaded successfully", "model": model_name}

        # Case 2 & 3: Not enough free VRAM - try to unload idle models
        logger.info(f"Insufficient VRAM ({vram_usage['free']:.2f} MiB). Attempting to free space...")
        
        # Get models that should NOT be unloaded
        active_models = get_active_models()
        queued_models = get_queued_models()
        protected_models = active_models.union(queued_models)
        
        logger.info(f"Protected models (active or queued): {protected_models}")
        
        # Build list of unloadable models with their sizes
        unloadable_models = []
        for model in loaded_models.get('models', []):
            model_name_loaded = model.get('name')
            
            if model_name_loaded in protected_models:
                logger.info(f"Skipping {model_name_loaded} - currently in use or queued")
                continue
            
            unloadable_models.append({
                'name': model_name_loaded,
                'size': model.get('size', 0) / (1024**2),
            })
        
        if not unloadable_models:
            logger.warning("No models available to unload - all are in use or queued")
            return {
                "status": "insufficient_vram",
                "message": "All loaded models are in use. Unable to free VRAM.",
                "model": model_name,
                "required_vram": model_size,
                "available_vram": vram_usage['free']
            }
        
        # Sort by size (unload smallest first)
        unloadable_models.sort(key=lambda x: x['size'])
        
        freed_space = 0
        unloaded_models = []
        
        for model in unloadable_models:
            if freed_space + vram_usage['free'] >= model_size:
                break
            
            try:
                ollama_client.generate(model=model['name'], prompt="", keep_alive=0)
                freed_space += model['size']
                unloaded_models.append(model['name'])
                logger.info(f"Unloaded model: {model['name']} ({model['size']:.2f} MiB)")
            except Exception as e:
                logger.warning(f"Failed to unload model {model['name']}: {str(e)}")
        
        if freed_space + vram_usage['free'] >= model_size:
            logger.info(f"Freed {freed_space:.2f} MiB. Loading model {model_name}")
            ollama_client.generate(model=model_name, prompt="", keep_alive=-1)
            return {
                "status": "loaded",
                "message": f"Model loaded after unloading {len(unloaded_models)} model(s)",
                "model": model_name,
                "unloaded": unloaded_models,
                "freed_vram": freed_space
            }
        
        # Still not enough space
        logger.warning(f"Unable to free sufficient VRAM for {model_name}")
        return {
            "status": "insufficient_vram",
            "message": "Unable to free sufficient VRAM",
            "model": model_name,
            "required_vram": model_size,
            "available_vram": vram_usage['free'] + freed_space,
            "unloaded": unloaded_models
        }

    except Exception as e:
        logger.exception(f"Error in load_or_queue_model: {str(e)}")
        return {"status": "error", "message": str(e)}

def cleanup_inactive_model_tracking():
    """Remove stale entries from Redis for models no longer needed"""
    try:
        r = get_redis_client()
        o = Client(host=OLLAMA_HOST)

        loaded_models = o.ps()
        loaded_model_names = {model.get('name') for model in loaded_models.get('models', [])}

        for key in r.scan_iter('active_model:*'):
            model_name = key.replace('active_model', '')

            if model_name not in loaded_model_names:
                r.delete(key)
                logger.info(f"Cleaned up stale tracking for unloaded model: {model_name}")
        
    except Exception as e:
        logger.error(f"Error in cleanup_inactive_model_tracking: {str(e)}")