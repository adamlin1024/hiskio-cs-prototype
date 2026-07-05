import json, collections

with open("data/conversations.json", encoding="utf-8") as f:
    data = json.load(f)

print("total conversations:", len(data))
print("keys of first:", list(data[0].keys()))
print()

# statuses
statuses = collections.Counter(c.get("status") for c in data)
print("statuses:", statuses)
print()

# message-level fields
from_vals = collections.Counter()
type_vals = collections.Counter()
kind_vals = collections.Counter()
msg_count_dist = collections.Counter()
empty_convs = 0
for c in data:
    msgs = c.get("messages") or []
    msg_count_dist[len(msgs)] += 1
    if not msgs:
        empty_convs += 1
    for m in msgs:
        from_vals[m.get("from")] += 1
        type_vals[m.get("type")] += 1
        sender = m.get("sender") or {}
        kind_vals[sender.get("kind")] += 1

print("from values:", from_vals)
print("type values:", type_vals)
print("sender.kind values:", kind_vals)
print("empty conversations:", empty_convs)
print()
print("msg count distribution (top 15):", msg_count_dist.most_common(15))
print()

# Print first 3 non-empty conversations in full
shown = 0
for c in data:
    msgs = c.get("messages") or []
    if len(msgs) >= 2:
        print("="*70)
        print("sessionId:", c.get("sessionId"), "status:", c.get("status"), "createdAt:", c.get("createdAt"))
        for m in msgs[:12]:
            sender = m.get("sender") or {}
            content = m.get("content")
            if isinstance(content, str):
                cprev = content[:200]
            else:
                cprev = repr(content)[:200]
            print(f"  [from={m.get('from')} type={m.get('type')} kind={sender.get('kind')} name={sender.get('name')!r}] {cprev}")
        shown += 1
    if shown >= 3:
        break
