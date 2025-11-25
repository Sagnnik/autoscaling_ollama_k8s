import streamlit as st
from uuid import uuid4
from worker.celery_app import ollama_stream
from services.redis_client import get_redis_client
import time

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
            message = pubsub.get_message(ignore_subscribe_messaegs = True, timeout=1)
            if message and message['type'] == 'message':
                data = message['data']
                if data == '[done]':
                    break
                yield data
            time.sleep(0.01)
    finally:
        pubsub.unsubscibe(channel_id)
        redis_client.close()

# Main UI
st.title("LLM Autoscaler")
if 'messages' not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if prompt := st.chat_input("what's up?"):
    # enqueue task
    ollama_stream.delay(prompt, channel_id)
    st.session_state.messages.append({'role': 'user', 'content': prompt})
    with st.chat_message('user'):
        st.markdown(prompt)

    with st.chat_message('assistant'):
        full_response = []

        def collect_stream(chunks):
            for chunk in chunks:
                full_response.append(chunk)
                yield chunk

        response = st.write_stream(lambda: collect_stream(streaming_data()))

    st.session_state.messages.append({"role": "assistant", "content": "".join(full_response)})