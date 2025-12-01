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
                data = message['data']
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

    try:
        o_client = Client(host=OLLAMA_HOST)
        pulled_models = o_client.list()
        model_list = set()
        for model in pulled_models.get('models', []):
            model_list.add(model.get('model'))

    except Exception as e:
        st.exception("Failed loading pulled model list", str(e))

    if model_list:
        model_name = st.selectbox("Select a model", model_list)
    else:
        model_name = st.selectbox("Select a model", ('qwen2.5:0.5b', 'qwen3:1.7b', 'qwen3:4b'))

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
