import streamlit as st
from uuid import uuid4
from ollama import Client
from worker.celery_app import process_ollama_request
from services.redis_client import get_redis_client
import time
import os
from dotenv import load_dotenv

load_dotenv()
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

st.set_page_config(
    page_title="LLM Autoscaler",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded"
)

if "channel_id" not in st.session_state:
    st.session_state.channel_id = uuid4().hex

channel_id = st.session_state.channel_id
def streaming_data():
    redis_client = get_redis_client()
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel_id)
    st.info(f"Subscribed to {channel_id}")
    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True)
            # prevents 100% CPU
            if not message:
                time.sleep(0.01)   
                continue

            if message and message['type'] == "message":
                raw = message['data']
                if isinstance(raw, bytes):
                    data = raw.decode('utf-8', errors='replace')
                else:
                    data = raw

                if data == '[DONE]':
                    break

                yield data
    finally:
        pubsub.unsubscribe(channel_id)
        pubsub.close()
        redis_client.close()

# Main UI
st.title("LLM Autoscaler")

with st.sidebar:
    if st.button('New Chat', type='primary'):
        st.session_state.messages = []
        st.session_state.channel_id = str(uuid4().hex)
        st.rerun()

    o_client = None
    try:
        o_client = Client(host=OLLAMA_HOST)
    except Exception as e:
        st.error(f"Failed to connect to Ollama at {OLLAMA_HOST}: {e}")
    st.subheader("Model management")

    default_models = ['qwen2.5:0.5b', 'qwen3:1.7b', 'qwen3:4b']
    preset_model = st.selectbox("Choose a preset model", default_models)
    custom_model = st.text_input("Or enter another model name", placeholder="e.g. llama3:8b")

    model_to_pull = custom_model.strip() if custom_model.strip() else preset_model

    if o_client is not None:
        if st.button("Pull model"):
            status_placeholder = st.empty()
            try:
                with st.spinner(f"Pulling `{model_to_pull}` from Ollama..."):
                    # stream=True returns progress chunks
                    for progress in o_client.pull(model=model_to_pull, stream=True):
                        # progress is typically a dict with fields like status, completed, total, digest
                        status = progress.get("status", "")
                        completed = progress.get("completed")
                        total = progress.get("total")
                        if completed is not None and total:
                            status_placeholder.write(
                                f"{status} - {completed}/{total} bytes"
                            )
                        else:
                            status_placeholder.write(status)

                st.success(f"Model `{model_to_pull}` pulled successfully ✅")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to pull model `{model_to_pull}`: {e}")

    model_list = set()
    if o_client is not None:
        try:
            pulled_models = o_client.list()
            for model in pulled_models.get('models', []):
                m = model.get('model')
                if m:
                    model_list.add(m)
        except Exception as e:
            st.error(f"Failed loading pulled model list: {e}")

    if model_list:
        model_name = st.selectbox("Select a model to chat with", sorted(model_list))
    else:
        model_name = st.selectbox(
            "Select a model to chat with",
            tuple(default_models)
        )

if 'messages' not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if prompt := st.chat_input("what's up?"):
    # enqueue task
    process_ollama_request.delay(query=prompt, channel_id=channel_id, model_name=model_name)
    st.session_state.messages.append({'role': 'user', 'content': prompt})
    with st.chat_message('user'):
        st.markdown(prompt)

    with st.chat_message('assistant'):
        full_response = []

        def collect_stream(chunks):
            for chunk in chunks:
                full_response.append(chunk)
                yield chunk

        response = st.write_stream(collect_stream(streaming_data()))

    st.session_state.messages.append({"role": "assistant", "content": "".join(full_response)})
