#!/usr/bin/env bash
# scrape_x.sh — X 推文抓取脚本
# 通过 agent-browser daemon + JS eval 提取推文

set -e

USERNAME="${1:-aleabitoreddit}"
MAX="${2:-30}"
AB_DIR="$HOME/.workbuddy/binaries/node/versions/22.22.2"
PY="C:/Users/eton/.workbuddy/binaries/python/versions/3.13.12/python.exe"
SESSION="x-stock-scraper"
OUTDIR="C:/Users/eton/WorkBuddy/2026-06-04-14-39-08/x-stock-sentiment/data"
mkdir -p "$OUTDIR"
OUTFILE="$OUTDIR/latest_tweets.json"

ab() { PATH="$AB_DIR:$PATH" agent-browser --session-name "$SESSION" "$@"; }
log() { echo "> $*" >&2; }

# ── Step 1: Navigate ──────────────────────────
log "Opening @${USERNAME} ..."
ab open "https://x.com/${USERNAME}" >&2
sleep 4

# ── Step 2: Extract with scrolling ────────────
JS_CONTENT=$(cat "C:/Users/eton/WorkBuddy/2026-06-04-14-39-08/x-stock-sentiment/scraper/extract_tweets.js")

echo '[]' > "$OUTFILE"

COUNT=0
PREV=0
SCROLLS=0
MAX_SCROLLS=20
STALL=0

while [ "$COUNT" -lt "$MAX" ] && [ "$SCROLLS" -lt "$MAX_SCROLLS" ] && [ "$STALL" -lt 3 ]; do
    # Extract
    BATCH=$(ab eval "$JS_CONTENT" 2>/dev/null)
    
    # Parse: agent-browser wraps in JSON string, need double parse
    "$PY" -c "
import json, sys, os

raw = sys.stdin.read().strip()
try:
    batch = json.loads(json.loads(raw))
except:
    batch = []

# Fix surrogate characters
def clean(s):
    if isinstance(s, str):
        return s.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
    return s

# Read existing
outfile_raw = os.environ.get('OUTFILE', r'C:/Users/eton/WorkBuddy/2026-06-04-14-39-08/x-stock-sentiment/data/latest_tweets.json')
with open(outfile_raw, 'r', encoding='utf-8') as f:
    existing = json.load(f)

seen = {t.get('id', '') or t['text'][:40] for t in existing}
for t in batch:
    tid = t.get('id', '') or t['text'][:40]
    if tid and tid not in seen and len(t.get('text', '')) > 5:
        seen.add(tid)
        t['created_at'] = t['time']
        del t['time']
        # Clean text
        t['text'] = clean(t['text'])
        existing.append(t)

with open(outfile_raw, 'w', encoding='utf-8') as f:
    json.dump(existing, f, ensure_ascii=False)
" <<< "$BATCH"
    
    COUNT=$("$PY" -c "import json; print(len(json.load(open(r'$OUTFILE', encoding='utf-8'))))")
    
    if [ "$COUNT" -eq "$PREV" ]; then
        STALL=$((STALL + 1))
    else
        STALL=0
    fi
    PREV=$COUNT
    
    log "scroll $SCROLLS: $COUNT tweets (stall=$STALL)"
    
    if [ "$COUNT" -ge "$MAX" ] || [ "$STALL" -ge 3 ]; then
        break
    fi
    
    # Human-like scroll
    dist=$((400 + RANDOM % 800))
    ab scroll down $dist >&2
    sleep $((2 + RANDOM % 3))
    SCROLLS=$((SCROLLS + 1))
done

# ── Step 3: Output ────────────────────────────
FINAL=$("$PY" -c "
import json
data = json.load(open(r'$OUTFILE', encoding='utf-8'))
print(len(data))
for t in data[:$MAX]:
    # Keep only needed fields
    print(json.dumps({'text': t['text'], 'created_at': t.get('created_at',''), 'id': t.get('id','')}, ensure_ascii=False))
")

COUNT=$(echo "$FINAL" | head -1)
log "Done: $COUNT tweets"

# Print as JSON array
echo "["
first=true
echo "$FINAL" | tail -n +2 | while read line; do
    if $first; then first=false; else echo ","; fi
    echo -n "  $line"
done
echo ""
echo "]"

# Close browser
ab close >&2 2>/dev/null || true
