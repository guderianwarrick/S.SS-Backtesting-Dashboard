"""批量拉取缺失股票的价格数据"""
import os, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from storage.models import init_db, session_scope, StockMention
from portfolio.price_fetcher import PriceFetcher
from sqlalchemy import func

def main():
    init_db()
    
    # 1. 获取所有独立 ticker
    with session_scope() as s:
        symbols = [r[0] for r in s.query(StockMention.symbol).distinct().all()]
    
    print(f"StockMentions 中独立 ticker 总数: {len(symbols)}")
    
    # 2. 检查缓存
    cache_dir = Path("data/price_cache")
    cached = set(f.stem for f in cache_dir.glob("*.json"))
    missing = [s for s in symbols if s not in cached]
    print(f"已有缓存: {len(cached)}")
    print(f"缺失: {len(missing)}\n")
    
    if not missing:
        print("✅ 没有缺失的 ticker！")
        return
    
    # 3. 按提及次数排序，优先拉高频的
    with session_scope() as s:
        counts = {}
        for sym in missing:
            cnt = s.query(func.count(StockMention.id)).filter(StockMention.symbol == sym).scalar()
            counts[sym] = cnt
    
    missing_sorted = sorted(missing, key=lambda x: -counts.get(x, 0))
    
    print("按提及次数排序的缺失 ticker (前20):")
    for sym in missing_sorted[:20]:
        print(f"  {sym:10s} 提及 {counts.get(sym, 0)} 次")
    if len(missing_sorted) > 20:
        print(f"  ... 还有 {len(missing_sorted)-20} 个")
    print()
    
    # 4. 批量拉取
    pf = PriceFetcher()
    success = 0
    failed = 0
    skipped = 0
    
    for i, sym in enumerate(missing_sorted):
        # 检查是否已有缓存（避免并行冲突）
        cache_path = cache_dir / f"{sym}.json"
        if cache_path.exists():
            skipped += 1
            continue
        
        try:
            added = pf.extend_cache(sym, target_start=date(2025, 7, 1))
            if added > 0:
                success += 1
                print(f"  [{i+1}/{len(missing_sorted)}] {sym}: +{added} 天")
            else:
                # 可能已有缓存但不在 cached set 里
                if cache_path.exists():
                    skipped += 1
                else:
                    failed += 1
                    print(f"  [{i+1}/{len(missing_sorted)}] {sym}: 无数据")
        except Exception as e:
            failed += 1
            print(f"  [{i+1}/{len(missing_sorted)}] {sym}: 错误 - {str(e)[:60]}")
        
        # 每 20 个打印进度
        if (i+1) % 20 == 0:
            print(f"\n  进度: {i+1}/{len(missing_sorted)}, 成功={success}, 失败={failed}, 跳过={skipped}\n")
    
    print(f"\n===== 完成 =====")
    print(f"成功: {success}, 失败: {failed}, 跳过: {skipped}")
    
    # 验证 SIVE
    sive_path = cache_dir / "SIVE.json"
    if sive_path.exists():
        import json
        data = json.loads(sive_path.read_text())
        dates = sorted(data.keys())
        print(f"\nSIVE 缓存: {len(data)} 天, 范围 {dates[0]} ~ {dates[-1]}")
    else:
        print(f"\n⚠️ SIVE 缓存未生成")

if __name__ == "__main__":
    main()