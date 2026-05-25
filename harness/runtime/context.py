from core.models import Message, SessionState
class ContextManager:
    def compact(self, state: SessionState, max_messages: int=24) -> SessionState:
        if len(state.messages)>max_messages:
            old=state.messages[:-max_messages]
            state.summary += '\n' + '\n'.join(f'{m.role}: {m.content[:180]}' for m in old)
            state.messages=state.messages[-max_messages:]
        return state
