from ollama import Client
import streamlit as st

model = 'qwen3:4b'
ollama_client = Client()
def ollama_stream(model:str, query:str):
    messages = [
        {
            'role': 'user',
            'content': query,
        }
    ]
    for chunk in ollama_client.chat(model=model, messages=messages, stream=True):
        yield chunk.message.response

st.title("LLM Autoscaler")
if 'messages' not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if prompt := st.chat_input("what's up?"):
    st.session_state.messages.append({'role': 'user', 'content': prompt})
    with st.chat_message('user'):
        st.markdown(prompt)

    with st.chat_message('assistant'):
        response = st.write_stream(ollama_stream(model, prompt))

    st.session_state.messages.append({"role": "assistant", "content": response})




