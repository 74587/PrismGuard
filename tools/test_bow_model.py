#!/usr/bin/env python3
"""
测试词袋模型预测
"""
import sys
sys.path.insert(0, '.')

from ai_proxy.moderation.smart.profile import get_profile
from ai_proxy.moderation.smart.bow import bow_predict_proba

def main():
    if len(sys.argv) < 3:
        print("用法: python tools/test_bow_model.py <profile_name> <text>")
        print("示例: python tools/test_bow_model.py 4claude '你好'")
        sys.exit(1)
    
    profile_name = sys.argv[1]
    text = sys.argv[2]
    
    profile = get_profile(profile_name)
    
    if not profile.bow_model_exists():
        print(f"❌ 模型不存在，请先训练: python tools/train_bow_model.py {profile_name}")
        sys.exit(1)
    
    print(f"测试文本: {text}")
    print(f"模型: {profile.get_model_path()}")
    print()
    
    try:
        prob = bow_predict_proba(text, profile)
        
        print(f"违规概率: {prob:.4f}")
        print()
        
        low_t = profile.config.probability.low_risk_threshold
        high_t = profile.config.probability.high_risk_threshold
        
        if prob < low_t:
            print(f"✅ 低风险 (< {low_t}) - 直接放行")
        elif prob > high_t:
            print(f"❌ 高风险 (> {high_t}) - 直接拒绝")
        else:
            print(f"⚠️  中等风险 ({low_t} ~ {high_t}) - 需要 AI 复核")
            
    except Exception as e:
        print(f"❌ 预测失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()