"""
基础文本审核模块 - 关键词过滤
"""
import os
import re
from typing import Optional, Tuple, List, Pattern


class KeywordFilter:
    """关键词过滤器"""
    
    def __init__(self, path: str):
        self.path = path
        self._mtime = 0
        self._patterns: List[Pattern] = []
        self.reload_if_needed()
    
    def reload_if_needed(self):
        """检查文件是否更新，如有更新则重新加载"""
        if not os.path.exists(self.path):
            self._patterns = []
            return
        
        mtime = os.path.getmtime(self.path)
        if mtime != self._mtime:
            self._mtime = mtime
            self._patterns = self._load_patterns()
    
    def _load_patterns(self) -> List[Pattern]:
        """加载关键词列表"""
        patterns = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    kw = line.strip()
                    # 跳过空行和注释
                    if not kw or kw.startswith("#"):
                        continue
                    # 将关键词转为正则表达式（转义特殊字符）
                    patterns.append(re.compile(re.escape(kw), re.IGNORECASE))
        except Exception as e:
            print(f"[ERROR] Failed to load keywords from {self.path}: {e}")
        return patterns
    
    def match(self, text: str) -> Optional[str]:
        """
        检查文本是否匹配任何关键词
        返回匹配的关键词，如果没有匹配则返回 None
        """
        self.reload_if_needed()
        for p in self._patterns:
            if p.search(text):
                return p.pattern
        return None


# 全局过滤器缓存
_filters = {}


def get_filter(keywords_file: str) -> KeywordFilter:
    """获取或创建关键词过滤器"""
    if keywords_file not in _filters:
        _filters[keywords_file] = KeywordFilter(keywords_file)
    return _filters[keywords_file]


def basic_moderation(text: str, cfg: dict) -> Tuple[bool, Optional[str]]:
    """
    基础审核
    
    Args:
        text: 待审核文本
        cfg: 审核配置
        
    Returns:
        (是否通过, 拒绝原因)
    """
    if not cfg.get("enabled", False):
        return True, None
    
    keywords_file = cfg.get("keywords_file", "configs/keywords.txt")
    filter_obj = get_filter(keywords_file)
    
    matched_kw = filter_obj.match(text)
    if matched_kw:
        error_code = cfg.get("error_code", "BASIC_MODERATION_BLOCKED")
        return False, f"[{error_code}] Matched keyword: {matched_kw}"
    
    return True, None