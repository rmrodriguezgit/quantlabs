import requests
from config import settings

class LlamaClient:
    def chat(self, messages: list[dict], temperature: float=.2, max_tokens: int | None = None):
        payload = {
            'messages': messages,
            'temperature': temperature,
            'stream': False,
            'max_tokens': max_tokens or settings.llm_max_tokens,
        }
        timeout = (settings.llm_connect_timeout_seconds, settings.llm_read_timeout_seconds)
        r = requests.post(f'{settings.llama_base_url}/v1/chat/completions', json=payload, timeout=timeout)
        r.raise_for_status()
        data=r.json()
        choice = data['choices'][0]
        return {
            'content': choice['message']['content'],
            'finish_reason': choice.get('finish_reason'),
            'usage': data.get('usage') or {},
            'timings': data.get('timings') or {},
            'model': data.get('model'),
        }
