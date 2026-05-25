from collections import deque
class DelegationQueue:
    def __init__(self): self._q=deque()
    def submit(self, task): self._q.append(task)
    def next(self): return self._q.popleft() if self._q else None
    def size(self): return len(self._q)
