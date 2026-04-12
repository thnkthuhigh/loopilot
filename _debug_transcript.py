import json
import sys

path = sys.argv[1]
with open(path) as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    ev = json.loads(line)
    t = ev.get('type', '?')
    data = ev.get('data', {})
    if t == 'user.message':
        text = (data.get('text', '') or '')[:200]
        mode = data.get('mode', '')
        print(f'{i}: {t} mode={mode} text="{text}"')
    elif t == 'assistant.message':
        content = (data.get('content', '') or '')[:200]
        tools = data.get('toolRequests', [])
        if tools:
            tool_names = [tr.get('toolName', '?') for tr in tools]
            print(f'{i}: {t} tools={tool_names}')
        else:
            print(f'{i}: {t} content="{content[:100]}"')
    elif t == 'tool.execution_start':
        tool_name = data.get('toolName', '?')
        print(f'{i}: {t} tool={tool_name}')
    elif t == 'tool.execution_complete':
        tool_name = data.get('toolName', '?')
        print(f'{i}: {t} tool={tool_name}')
    else:
        print(f'{i}: {t}')

print(f'\nTotal lines: {len(lines)}')
print(f'Last event: {json.loads(lines[-1]).get("type")}')
