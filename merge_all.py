#!/usr/bin/env python3
"""
重新合并所有推文数据，修复时间解析问题。
"""
import json
from datetime import datetime
from pathlib import Path

def parse_time(created):
    """解析时间字符串。"""
    if not created:
        return None
    
    try:
        # ISO 格式: 2025-07-02T10:48:09.000Z
        # 检查是否有 'T' 作为日期时间分隔符（不是星期缩写中的 T）
        if 'T' in created and '-' in created.split('T')[0]:
            return datetime.fromisoformat(created.replace('Z', '+00:00'))
        # Twitter 格式: Wed Jun 17 15:46:21 +0000 2026
        else:
            return datetime.strptime(created, '%a %b %d %H:%M:%S %z %Y')
    except Exception as e:
        return None


def main():
    print('=' * 70)
    print('重新合并所有推文数据')
    print('=' * 70)
    
    # 所有数据文件
    files = [
        'data/desearch_all_tweets.json',
        'data/tweets_merged.json',
        'data/tweets_cookie.json',
    ]
    
    all_tweets = {}
    parse_ok = 0
    parse_fail = 0
    
    for fname in files:
        try:
            data = json.load(open(fname))
            print(f'\n{fname}: {len(data)} 条')
            
            for t in data:
                tid = str(t.get('id', ''))
                if not tid:
                    continue
                
                # 解析时间
                created = t.get('created_at', '')
                dt = parse_time(created)
                
                if dt:
                    parse_ok += 1
                else:
                    parse_fail += 1
                    if parse_fail <= 3:
                        print(f'  时间解析失败: {created[:40]}')
                
                # 去重
                if tid not in all_tweets:
                    all_tweets[tid] = t
        except Exception as e:
            print(f'  错误: {e}')
    
    print(f'\n' + '=' * 70)
    print(f'合并结果:')
    print(f'  唯一推文: {len(all_tweets)} 条')
    print(f'  时间解析成功: {parse_ok}')
    print(f'  时间解析失败: {parse_fail}')
    
    # 按时间排序
    tweets_list = list(all_tweets.values())
    tweets_with_time = []
    tweets_without_time = []
    
    for t in tweets_list:
        dt = parse_time(t.get('created_at', ''))
        if dt:
            tweets_with_time.append((dt, t))
        else:
            tweets_without_time.append(t)
    
    tweets_with_time.sort(key=lambda x: x[0])
    sorted_tweets = [t for _, t in tweets_with_time] + tweets_without_time
    
    # 保存
    output_file = Path('data/tweets_full_fixed.json')
    output_file.write_text(json.dumps(sorted_tweets, ensure_ascii=False, indent=2))
    
    print(f'\n保存到: {output_file}')
    
    # 显示时间范围
    if tweets_with_time:
        dates = [dt for dt, _ in tweets_with_time]
        print(f'时间范围: {dates[0].strftime("%Y-%m-%d")} ~ {dates[-1].strftime("%Y-%m-%d")}')
    
    print('=' * 70)


if __name__ == '__main__':
    main()
