#!/usr/bin/env bash
# X 推文抓取脚本 — 通过 agent-browser CLI
# 用法: bash scrape_tweets.sh <username> [max_tweets]
# 输出: JSON 数组到 stdout

set -e

USERNAME="${1:-aleabitoreddit}"
MAX_TWEETS="${2:-30}"

AB_DIR="$HOME/.workbuddy/binaries/node/versions/22.22.2"
AB="$AB_DIR/agent-browser"
PROXY="http://127.0.0.1:7897"
SESSION="x-stock-scraper"

log() { echo "[$(date +%H:%M:%S)] $*" >&2; }

# 启动 agent-browser daemon
ab_cmd() {
    PATH="$AB_DIR:$PATH" "$AB" --session-name "$SESSION" --proxy "$PROXY" "$@"
}

log "打开 @${USERNAME} 时间线..."
ab_cmd open "https://x.com/${USERNAME}" >&2
sleep 4

all_tweets="[]"
seen_ids=""
scrolls=0
max_scrolls=$((MAX_TWEETS / 3 > 5 ? MAX_TWEETS / 3 : 5))
stall=0

JS_EXTRACT='(() => {
  const tweets = document.querySelectorAll("[data-testid=\"tweet\"]");
  return JSON.stringify(Array.from(tweets).map(t => {
    const textEl = t.querySelector("[data-testid=\"tweetText\"]");
    const timeEl = t.querySelector("time");
    const linkEl = t.querySelector("a[href*=\"/status/\"]");
    let text = "";
    if (textEl) {
      text = textEl.innerText;
    } else {
      const lines = t.innerText.split("\n").filter(l =>
        l && l.length > 10 &&
        !l.startsWith("@") &&
        !l.includes("关注") && !l.includes("回复") &&
        !l.includes("引用") && !l.includes("转帖") &&
        !l.includes("显示") && !l.includes("查看") &&
        !l.includes("已置顶") && !l.match(/^\d/)
      );
      text = lines.slice(0, 4).join(" ");
    }
    return {
      text: text,
      created_at: timeEl ? timeEl.getAttribute("datetime") : "",
      url: linkEl ? linkEl.href : "",
      id: (linkEl ? linkEl.href : "").split("/status/")[1]?.split("?")[0] || ""
    };
  }));
})()'

while [ $scrolls -lt $max_scrolls ] && [ $stall -lt 3 ]; do
    # 提取推文
    batch=$(ab_cmd eval "$JS_EXTRACT" 2>/dev/null)
    count=$(echo "$batch" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    
    log "  第 $((scrolls+1)) 次滚动: 可见 $count 条推文"
    
    # 合并去重
    all_tweets=$(echo "$all_tweets" "$batch" | python3 -c "
import sys, json
existing = json.loads(sys.argv[1])
new = json.loads(sys.argv[2])
seen = {t.get('id','') or t['text'][:50] for t in existing}
for t in new:
    tid = t.get('id','') or t['text'][:50]
    if tid and tid not in seen and len(t['text']) > 5:
        seen.add(tid)
        existing.append(t)
print(json.dumps(existing, ensure_ascii=False))
" "$all_tweets" "$batch")

    current_count=$(echo "$all_tweets" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    
    if [ "$current_count" -ge "$MAX_TWEETS" ]; then
        break
    fi
    
    # 上次滚动没新推文?
    if [ "$current_count" = "$prev_count" ]; then
        stall=$((stall + 1))
    else
        stall=0
    fi
    prev_count=$current_count
    
    # 人类化滚动
    dist=$((600 + RANDOM % 600))
    ab_cmd scroll down $dist >&2
    sleep $((2 + RANDOM % 3))
    scrolls=$((scrolls + 1))
done

# 截断到 max_tweets
result=$(echo "$all_tweets" | python3 -c "import sys,json; data=json.load(sys.stdin); print(json.dumps(data[:${MAX_TWEETS}], ensure_ascii=False))")

log "完成: 共 $(echo "$result" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))") 条推文"

# 输出 JSON 结果
echo "$result"

# 关闭浏览器
ab_cmd close >&2 2>/dev/null || true
