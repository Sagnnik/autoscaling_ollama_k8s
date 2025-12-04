# Autoscaling Ollama on Kubernetes

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)
![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)

An intelligent, resource-aware serving platform for Ollama models, designed for scalability and efficient GPU utilization. This project provides a complete solution for dynamically loading, serving, and scaling large language models, with a roadmap for deployment on Kubernetes.

## Table of Contents
- [Key Features](#key-features)
- [Architecture Overview](#architecture-overview)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [Advanced VRAM Management](#advanced-vram-management)
- [Roadmap](#roadmap)
- [Project Progress](#project-progress)
- [Contributing](#contributing)
- [License](#license)

## Key Features

- **Dynamic Model Loading/Unloading**: Models are loaded into VRAM on-demand and automatically unloaded when no longer needed.
- **Intelligent VRAM Management**: When a new model needs to be loaded, the system automatically offloads the least-recently-used models to free up VRAM. It uses a **knapsack subset search algorithm** to find the optimal set of inactive models to evict, minimizing the number of offloads.
- **Scalable Asynchronous Task Queue**: Leverages Celery workers to handle model inference requests asynchronously, preventing the UI from blocking and allowing for horizontal scaling.
- **Real-time Chat Interface**: A user-friendly web interface built with Streamlit that supports multiple chats, model selection, and real-time streaming of responses.
- **GPU Resource Aware**: Actively monitors VRAM usage to make intelligent decisions about model loading and eviction.
- **Extensible and Containerized**: The entire application stack is containerized with Docker and orchestrated with Docker Compose, making it easy to set up and deploy.

## Architecture Overview

The system is composed of several microservices that work together:

- **FastAPI Backend**: A central API server that exposes endpoints for chat, model management, and task status.
- **Streamlit**: The web-based frontend and user interface for interacting with the models. It consumes the FastAPI backend.
- **Ollama**: The core server that runs the large language models.
- **Celery Workers**: Background workers that handle the heavy lifting of model loading and inference requests.
- **Redis**: Acts as the message broker for Celery and a cache for storing application state (e.g., which models are active, queued, or reserved).
- **Celery Beat**: A scheduler for periodic tasks, such as cleaning up stale model tracking data.
- **Flower**: A monitoring tool for inspecting the status of Celery workers and tasks.

```
+----------------+      +------------------+      +------------------+      +----------------+
| User's Browser |----->|     Streamlit    |----->|  FastAPI Backend |----->|     Redis      |
+----------------+      | (Web Frontend)   |      |   (API Server)   |      | (Broker/Cache) |
                        +------------------+      +------------------+      +----------------+
                                                      |         ^                  ^
                                                      | (Celery)|              (Pub/Sub)      
                                                      v         |                  |
                                                +------------------+      +------------------+
                                                | Celery Worker    |----->|     Ollama       |
                                                | (Model/Task Proc)|      | (LLM Server/GPU) |
                                                +------------------+      +------------------+
```

## Getting Started

Follow these instructions to get the project running on your local machine.

### Prerequisites

- Docker and Docker Compose
- NVIDIA GPU with the NVIDIA Container Toolkit installed.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd autoscaling_ollama_k8s
    ```

2.  **Configure the environment:**
    Create a `.env` file by copying the example file if one is provided, or create one from scratch. At a minimum, you may need to specify the Ollama and Redis hosts if you are not using the defaults from `docker-compose.yml`.
    ```env
    OLLAMA_HOST=http://ollama:11434
    REDIS_URL=redis://redis:6379/0
    ```

3.  **Build and run the services:**
    ```bash
    docker-compose up --build
    ```
    This command will build the Docker images and start all the services defined in the `docker-compose.yml` file.

## Usage

### Chat Interface

Once the services are running, open your web browser and navigate to `http://localhost:8501`.

You will be greeted with the main chat interface. From here, you can:
- Start a new chat.
- Select a model to chat with from the dropdown.
- View your chat history in the sidebar.

*<-- Placeholder for a screenshot of the main chat interface -->*

### API Usage

The backend API is available at `http://localhost:8000`.

**Get a list of available models:**
```bash
curl -X GET "http://localhost:8000/api/v1/models"
```

**Pull a new model:**
```bash
curl -X POST "http://localhost:8000/api/v1/pull" \
-H "Content-Type: application/json" \
-d '{"model_name": "llama3:8b"}'
```

**Start a chat session:**
```bash
curl -X POST "http://localhost:8000/api/v1/chat" \
-H "Content-Type: application/json" \
-d '{
    "query": "Why is the sky blue?",
    "model_name": "llama3:8b",
    "channel_id": "my-unique-channel-id"
}'
```
This will return a `task_id`. You can use this to check the status of the task.

**Check task status:**
```bash
curl -X GET "http://localhost:8000/api/v1/task/{task_id}"
```

### Monitoring with Flower

To monitor the Celery workers and see the status of background tasks, navigate to `http://localhost:5555`.

*<-- Placeholder for a screenshot of the Flower dashboard -->*

## Advanced VRAM Management

A key feature of this project is its intelligent management of GPU VRAM. When a request for a model arrives and there isn't enough free VRAM to load it, the system doesn't just fail. Instead, it performs the following steps:

1.  **Identify Inactive Models**: The system identifies all models currently loaded in VRAM that are not actively processing a request or reserved by a queued task.
2.  **Find the Optimal Eviction Set**: It then uses a brute-force knapsack algorithm to find the smallest combination of inactive models whose combined size is just enough to free the required space. This is more efficient than evicting models one-by-one until enough space is available.
3.  **Evict and Load**: The selected models are evicted from VRAM, and the new model is loaded.
4.  **Queue if Necessary**: If not enough space can be freed even after evicting all inactive models, the request is queued and will be retried automatically.

This process ensures maximum utilization of GPU resources and allows for serving a larger variety of models than can fit in VRAM simultaneously.

## Roadmap

The current focus is on finalizing the Kubernetes deployment scripts and configurations.

-   **[ ] Create K8s deployment:**
    -   Define deployments for all services (Streamlit, Celery, Ollama, Redis).
    -   Configure Horizontal Pod Autoscalers (HPA) for Celery workers.
    -   Set up persistent volumes for Ollama models.
    -   Testing deployments on a managed Kubernetes service (Digital Ocean).

## Project Progress

<details>
<summary>View a high-level summary of completed and ongoing tasks</summary>

```
[ x ] Run App in Docker Compose
		- Added Streamlit app 
		- Added redis pub/sub
		- Added celery worker with ollama streaming task
		- Added flower for worker tracking
		- Added model router
	[ x ] Adding Model Loading/Unloading Management
		- get gpu info
		- get model info
		- track models in redis cache
		- model loading/unloading logic
		- add celery tasks for both streaming, loading, cleanup
		- add celery beat in docker compose
	[ x ] Use knapsack subset search instead of offload small model first
	[ x ] Implement redis locks
	[ x ] UI improvements
		- Enable model pull
		- New chat and chat history
	[ x ] Decouple backend and frontend
		- Added FastAPI backend
		- Streamlit uses the backend
	[ ] Create k8s deployment
```
</details>

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue to discuss your ideas.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
