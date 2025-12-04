import streamlit as st
from uuid import uuid4
import requests
from services.redis_client import get_redis_client
import time
import os
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

st.set_page_config(
    page_title="LLM Autoscaler",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="expanded"
)

def init_state():
    if "chats" not in st.session_state:
        # chats: {chat_id: {"title": str, "messages": [...], "channel_id": str}}
        first_chat_id = uuid4().hex
        st.session_state.chats = {
            first_chat_id: {
                "title": "New chat",
                "messages": [],
                "channel_id": uuid4().hex,
            }
        }
        st.session_state.current_chat_id = first_chat_id

init_state()

def get_current_chat():
    chat_id = st.session_state.current_chat_id
    return chat_id, st.session_state.chats[chat_id]

def create_new_chat():
    chat_id = uuid4().hex
    st.session_state.chats[chat_id] = {
        "title": "New chat",
        "messages": [],
        "channel_id": uuid4().hex,
    }
    st.session_state.current_chat_id = chat_id

def rename_chat_if_needed(chat, first_user_message: str):
    if chat["title"] == "New chat":
        chat["title"] = first_user_message[:30] + ("…" if len(first_user_message) > 30 else "")

def streaming_data(channel_id: str):
    redis_client = get_redis_client()
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel_id)
    try:
        while True:
            message = pubsub.get_message(ignore_subscribe_messages=True)
            # prevents 100% CPU
            if not message:
                time.sleep(0.01)
                continue

            if message and message['type'] == "message":
                raw = message['data']
                data = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw

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
    if st.button('New Chat', type='primary', use_container_width=True):
        create_new_chat()
        st.rerun()

    st.subheader("Model management")

    default_models = ['qwen2.5:0.5b']
    custom_model = st.text_input("Enter the name of the model to Pull", placeholder="e.g. llama3:8b")
    model_to_pull = custom_model.strip() if custom_model.strip() else default_models[0]

    if st.button("Pull model"):
        try:
            with st.spinner(f"Requesting to pull `{model_to_pull}`..."):
                response = requests.post(f"{API_BASE_URL}/api/v1/pull", json={"model_name": model_to_pull})
                response.raise_for_status()
            st.success(f"Pull request for `{model_to_pull}` sent successfully! It will be available shortly.")
            time.sleep(2)
            st.rerun()
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to send pull request for `{model_to_pull}`: {e}")

    model_list = set()
    try:
        response = requests.get(f"{API_BASE_URL}/api/v1/models")
        response.raise_for_status()
        pulled_models = response.json()
        model_list.update(pulled_models)
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to fetch model list from API: {e}")

    if model_list:
        model_name = st.selectbox("Select a model to chat with", sorted(model_list))
    else:
        model_name = st.selectbox("Select a model to chat with", tuple(default_models))

    st.divider()
    st.header("History")
    current_chat_id, current_chat = get_current_chat()
    for chat_id, chat in reversed(list(st.session_state.chats.items())):
        is_current = (chat_id == current_chat_id)
        label = ("🟢 " if is_current else "⚪ ") + (chat["title"] or "Untitled")
        if st.button(label, key=f"chat_btn_{chat_id}", use_container_width=True):
            st.session_state.current_chat_id = chat_id
            st.rerun()

current_chat_id, current_chat = get_current_chat()

for message in current_chat["messages"]:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if prompt := st.chat_input("what's up?"):
    rename_chat_if_needed(current_chat, prompt)
    channel_id = current_chat["channel_id"]

    try:
        requests.post(
            f"{API_BASE_URL}/api/v1/chat",
            json={"query": prompt, "model_name": model_name, "channel_id": channel_id},
        ).raise_for_status()

        current_chat["messages"].append({'role': 'user', 'content': prompt})
        with st.chat_message('user'):
            st.markdown(prompt)

        with st.chat_message('assistant'):
            response = st.write_stream(streaming_data(channel_id))

        current_chat["messages"].append({"role": "assistant", "content": response})

    except requests.exceptions.RequestException as e:
        st.error(f"Failed to send message: {e}")