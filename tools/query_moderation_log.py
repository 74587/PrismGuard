#!/usr/bin/env python3
"""
查询审核日志工具
"""
import sqlite3
import sys
from datetime import datetime

def query_logs(db_path: str, limit: int = 20):
    """查询最近的审核记录"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 查询总数
    cursor.execute("SELECT COUNT(*) FROM samples")
    total = cursor.fetchone()[0]
    print(f"总记录数: {total}\n")
    
    # 查询最近的记录
    cursor.execute("""
        SELECT id, text, label, category, created_at 
        FROM samples 
        ORDER BY id DESC 
        LIMIT ?
    """, (limit,))
    
    records = cursor.fetchall()
    
    print(f"最近 {len(records)} 条记录:")
    print("="*80)
    
    for record in records:
        id, text, label, category, created_at = record
        label_str = "❌ 违规" if label == 1 else "✅ 通过"
        text_preview = text[:100] + "..." if len(text) > 100 else text
        
        print(f"\nID: {id} | {label_str} | 类别: {category or 'N/A'}")
        print(f"时间: {created_at}")
        print(f"文本: {text_preview}")
        print("-"*80)
    
    # 统计
    cursor.execute("""
        SELECT label, COUNT(*) 
        FROM samples 
        GROUP BY label
    """)
    stats = cursor.fetchall()
    
    print(f"\n统计:")
    for label, count in stats:
        label_str = "违规" if label == 1 else "通过"
        print(f"  {label_str}: {count} 条 ({count/total*100:.1f}%)")
    
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python query_moderation_log.py <db_path> [limit]")
        print("示例: python query_moderation_log.py configs/mod_profiles/4claude/history.db 20")
        sys.exit(1)
    
    db_path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    
    query_logs(db_path, limit)