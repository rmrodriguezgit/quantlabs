import requests
from config import settings

class LlamaClient:
    def chat(self, messages: list[dict], temperature: float=.2):
        payload = {
            'messages': messages,
            'temperature': temperature,
            'stream': False,
            'max_tokens': settings.llm_max_tokens,
        }
        timeout = (settings.llm_connect_timeout_seconds, settings.llm_read_timeout_seconds)
        r = requests.post(f'{settings.llama_base_url}/v1/chat/completions', json=payload, timeout=timeout)
        r.raise_for_status()
        data=r.json()
        return {
            'content': data['choices'][0]['message']['content'],
            'usage': data.get('usage') or {},
            'timings': data.get('timings') or {},
            'model': data.get('model'),
        }
