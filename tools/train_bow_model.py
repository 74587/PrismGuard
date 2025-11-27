#!/usr/bin/env python3
"""
词袋模型训练工具
"""
import sys
sys.path.insert(0, '.')

from ai_proxy.moderation.smart.profile import get_profile
from ai_proxy.moderation.smart.bow import train_bow_model

def main():
    if len(sys.argv) < 2:
        print("用法: python tools/train_bow_model.py <profile_name>")
        print("示例: python tools/train_bow_model.py 4claude")
        sys.exit(1)
    
    profile_name = sys.argv[1]
    profile = get_profile(profile_name)
    
    print(f"开始训练 {profile_name} 的词袋模型...")
    print(f"数据库: {profile.get_db_path()}")
    print(f"模型输出: {profile.get_model_path()}")
    print(f"向量化器输出: {profile.get_vectorizer_path()}")
    print()
    
    try:
        train_bow_model(profile)
        print("\n✅ 训练完成！")
    except Exception as e:
        print(f"\n❌ 训练失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()