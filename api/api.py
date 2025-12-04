from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from celery.result import AsyncResult
from contextlib import asynccontextmanager
from pydantic import BaseModel
from typing import List
from services.logger import logger
from services.redis_client import get_redis_client
from worker.celery_app import process_ollama_request

import ollama
from dotenv import load_dotenv
import os

load_dotenv()
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://localhost:11434')


class ChatRequest(BaseModel):
    query: str
    model_name: str
    channel_id:str

class PullRequest(BaseModel):
    model_name:str

ollama_client = None
@asynccontextmanager
async def lifespan(app: FastAPI):
    global ollama_client
    logger.info("[Ollama Client] Connecting...")
    ollama_client = ollama.Client(host=OLLAMA_HOST)

    yield

    logger.info("[Ollama Client] Disconnected.")
    ollama_client = None

app = FastAPI(title="Autoscaled Ollama Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    if ollama_client is None:
        return JSONResponse(
            status_code=503,
            content={"status": "fail", "reason": "ollama_client_not_initialized"},
        )
    
    try:
        redis_client = get_redis_client()
        redis_client.ping()
    except Exception as e:
        logger.exception("[Health] Redis unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "fail", "reason": f"redis_unreachable: {str(e)}"},
        )
    return {"status": "ok"}
        

@app.get("/api/v1/models", response_model=List[str])
async def get_models():
    """Lists all the currently pulled models"""
    if ollama_client is None:
        logger.exception("[Ollama CLient] Is not connected")
        raise HTTPException(status_code=503, detail="Ollama client not initialized")
    
    try:
        pulled_models = ollama_client.list()
        model_list = []
        for model in pulled_models.get('models', []):
            if model.get('model'):
                model_list.append(model['model'])

        return model_list
    
    except Exception as e:
        logger.exception("[Ollama Client] Cannot get pulled model info")
        raise HTTPException(status_code=500, detail=f"Error connecting to Ollama: {str(e)}")
    
@app.post("/api/v1/pull")
async def pull_model(request: PullRequest):
    """Pull a new model from Ollama Hub"""
    if ollama_client is None:
        logger.exception("[Ollama CLient] Is not connected")
        raise HTTPException(status_code=503, detail="Ollama client not initialized")
    
    try:
        logger.info("[Ollama Client] Starting to pull model")
        ollama_client.pull(model=request.model_name, stream=False) # Progress streams needs SSE or WS
        return JSONResponse({
            "status": "success",
            "message": f"Pulling model '{request.model_name}' has started."
        })
    
    except Exception as e:
        logger.error("[Ollama Client] Failed to pull the model")
        raise HTTPException(status_code=500, detail=f"Failed to pull model: {str(e)}")
    
@app.post("/api/v1/chat")
async def chat_endpoint(request:ChatRequest):
    """Chat endpoint that recieves the chat query and sends it to celery workers"""
    try:
        logger.info("[chat_endpoint] Despatching task to Celery")
        task = process_ollama_request.delay(
            query=request.query, 
            model_name=request.model_name, 
            channel_id=request.channel_id
        )
        return JSONResponse({
            "status": "queued", 
            "task_id": task.id, 
            "channel_id": request.channel_id
        })
    
    except Exception as e:
        logger.exception("[chat_endpoint] Failed to despatch task to Celery")
        raise HTTPException(status_code=500, detail=f"Failed to queue chat request: {str(e)}")

@app.get("/api/v1/task/{task_id}")
async def task_status(task_id: str):
    """Get the celery task status"""
    result = AsyncResult(task_id)
    return JSONResponse({
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None
    })


