import sys, json, os

d = json.load(sys.stdin)
tp = d.get('transcript_path', '')
agents = 0
tools = 0

if tp and os.path.exists(tp):
    with open(tp) as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get('type') == 'assistant':
                    for b in e.get('message', {}).get('content', []):
                        if b.get('type') == 'tool_use':
                            tools += 1
                            if b.get('name') == 'Task':
                                agents += 1
            except:
                pass

print(f'{agents} {tools}')
