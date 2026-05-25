from core.models import SessionState

def test_state_serializes(): assert SessionState(session_id='x').model_dump()['session_id']=='x'
