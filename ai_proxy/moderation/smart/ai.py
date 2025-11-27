"""
AI 审核模块 - 支持三段式决策
"""
import os
import json
import random
from typing import Tuple, Optional
from pydantic import BaseModel
from openai import AsyncOpenAI

from ai_proxy.moderation.smart.profile import get_profile, ModerationProfile
from ai_proxy.moderation.smart.storage import SampleStorage


class ModerationResult(BaseModel):
    """审核结果"""
    violation: bool
    category: Optional[str] = None
    reason: Optional[str] = None
    source: str  # "ai" or "bow_model"
    confidence: Optional[float] = None


async def ai_moderate(text: str, profile: ModerationProfile) -> ModerationResult:
    """使用 AI 进行审核"""
    api_key = os.getenv(profile.config.ai.api_key_env)
    if not api_key:
        raise ValueError(f"Environment variable {profile.config.ai.api_key_env} not set")
    
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=profile.config.ai.base_url,
        timeout=profile.config.ai.timeout
    )
    
    prompt = profile.render_prompt(text)
    
    try:
        response = await client.chat.completions.create(
            model=profile.config.ai.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        
        content = response.choices[0].message.content
        
        # 解析 JSON
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
        else:
            violation = any(word in content.lower() for word in ["违规", "violation", "不当"])
            data = {"violation": violation, "category": "unknown", "reason": content[:200]}
        
        return ModerationResult(
            violation=data.get("violation", False),
            category=data.get("category"),
            reason=data.get("reason"),
            source="ai"
        )
    
    except Exception as e:
        print(f"[ERROR] AI moderation failed: {e}")
        return ModerationResult(
            violation=False,
            category="error",
            reason=f"AI call failed: {str(e)}",
            source="ai"
        )


async def run_ai_moderation_and_log(text: str, profile: ModerationProfile) -> ModerationResult:
    """AI 审核并记录结果"""
    result = await ai_moderate(text, profile)
    
    # 保存到数据库
    storage = SampleStorage(profile.get_db_path())
    label = 1 if result.violation else 0
    storage.save_sample(text, label, result.category)
    
    return result


async def smart_moderation(text: str, cfg: dict) -> Tuple[bool, Optional[ModerationResult]]:
    """
    智能审核入口 - 三段式决策
    
    流程：
    1. 随机抽样 -> AI 审核并记录
    2. 本地词袋模型 -> 低风险放行 / 高风险拒绝 / 中间交 AI
    3. 无模型 -> 全部交 AI
    """
    if not cfg.get("enabled", False):
        return True, None
    
    profile_name = cfg.get("profile", "default")
    profile = get_profile(profile_name)
    
    random.seed(profile.config.probability.random_seed)
    
    ai_rate = profile.config.probability.ai_review_rate
    
    # 1. 随机抽样：直接走 AI（用于持续产生标注）
    if random.random() < ai_rate:
        result = await run_ai_moderation_and_log(text, profile)
        return not result.violation, result
    
    # 2. 尝试本地词袋模型
    if profile.bow_model_exists():
        from ai_proxy.moderation.smart.bow import bow_predict_proba
        
        try:
            p = bow_predict_proba(text, profile)
            low_t = profile.config.probability.low_risk_threshold
            high_t = profile.config.probability.high_risk_threshold
            
            # 低风险：直接放行
            if p < low_t:
                result = ModerationResult(
                    violation=False,
                    reason=f"BoW: low risk (p={p:.3f})",
                    source="bow_model",
                    confidence=p
                )
                return True, result
            
            # 高风险：直接拒绝
            if p > high_t:
                result = ModerationResult(
                    violation=True,
                    reason=f"BoW: high risk (p={p:.3f})",
                    source="bow_model",
                    confidence=p
                )
                return False, result
            
            # 不确定：交给 AI 复核
            result = await run_ai_moderation_and_log(text, profile)
            return not result.violation, result
            
        except Exception as e:
            print(f"[WARN] BoW prediction failed: {e}, fallback to AI")
    
    # 3. 无模型或失败：全部交 AI
    result = await run_ai_moderation_and_log(text, profile)
    return not result.violation, result