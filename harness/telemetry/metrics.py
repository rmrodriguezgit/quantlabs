from prometheus_client import Counter, Histogram, Gauge
TOOL_CALLS = Counter('tool_calls_total','Tool calls',['tool','ok'])
TOOL_LATENCY = Histogram('tool_latency_seconds','Tool latency',['tool'])
AGENT_RUNS = Counter('agent_runs_total','Agent runs',['agent','ok'])
ACTIVE_SESSIONS = Gauge('active_sessions','Active sessions')
