import requests
from policies.security import WebPolicy
from .base import BaseTool
class WebAPITool(BaseTool):
    name='web_api'
    def __init__(self): self.policy=WebPolicy()
    def run(self, method: str, url: str, **kwargs):
        self.policy.validate(url)
        if method.upper() not in {'GET','HEAD'}: raise PermissionError('HTTP method not allowed')
        resp=requests.request(method.upper(), url, timeout=30, **kwargs)
        return {'status':resp.status_code,'headers':dict(resp.headers),'text':resp.text[:12000]}
