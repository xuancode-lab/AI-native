"""规则集分类器：判断层的 MVP 默认实现。

对应 "jev 设计思路"（一次分类、判断不重读上下文）的朴素落地：
用一组可扩展的规则，对"已抽取/清洗后的元信息"做单次判断，产出 topic/type/priority，
并把判断依据(verdict)持久化，后续同类判断直接命中缓存，不再重读原文。
"""
from __future__ import annotations


# 主题关键词表：命中即归入该主题（可随知识领域扩展）
TOPIC_RULES = {
    "技术": ["python", "代码", "算法", "架构", "api", "bug", "数据库", "后端",
             "前端", "linux", "docker", "模型", "训练", "token", "gpt", "claude"],
    "学习": ["学习", "笔记", "课程", "读书", "考试", "教程", "方法", "知识点"],
    "课题": ["课题", "研究", "论文", "综述", "实验", "数据", "假设", "参考文献"],
    "创作": ["创作", "写作", "文案", "脚本", "故事", "大纲", "素材"],
    "生活": ["生活", "食谱", "健康", "旅行", "费用", "购物", "习惯"],
}

# 类型：按结构特征判断
TYPE_RULES = {
    "百科": ["术语表", "定义", "概念", "总述"],
    "教程": ["步骤", "如何", "教程", "操作", "指南"],
    "随笔": ["随笔", "感想", "记录"],
    "问答": ["问答", "Q&A", "FAQ", "为什么"],
}

# 优先级权重词（命中越多越高）
PRIORITY_HINTS = {
    "核心": 3, "重要": 2, "高频": 2, "必读": 3, "基础": 1, "进阶": 2, "冷门": 0,
}


class RuleClassifier:
    name = "rules"

    def __init__(self, extra_topics: dict[str, list[str]] | None = None):
        """extra_topics：用户新增分类（来自 categories 表），叠加到内置 TOPIC_RULES。"""
        self.topics = dict(TOPIC_RULES)
        if extra_topics:
            self.topics.update(extra_topics)

    def classify(self, text: str) -> dict:
        """单次判断，返回 topic/type/priority 及依据 verdict。

        :param text: 已清洗的正文（可能是全文本，判断层只读一次）。
        """
        text_lower = text.lower()
        verdict: dict = {}

        # 主题：取命中权重最高者
        best_topic, best_hits = "未分类", 0
        for topic, kws in self.topics.items():
            hits = sum(1 for k in kws if k.lower() in text_lower)
            if hits > best_hits:
                best_topic, best_hits = topic, hits
        verdict["topic_hits"] = {t: sum(1 for k in ks if k.lower() in text_lower)
                                 for t, ks in self.topics.items() if any(k.lower() in text_lower for k in ks)}

        # 类型
        best_type, t_hits = "笔记", 0
        for t, kws in TYPE_RULES.items():
            hits = sum(1 for k in kws if k.lower() in text_lower)
            if hits > t_hits:
                best_type, t_hits = t, hits
        verdict["type_hits"] = t_hits

        # 优先级
        prio = 0
        for hint, w in PRIORITY_HINTS.items():
            if hint in text:
                prio += w
        prio = min(5, max(0, prio))

        return {
            "topic": best_topic,
            "type": best_type,
            "priority": prio,
            "verdict": verdict,
        }

    def classify_metadata(self, text: str, title: str) -> dict:
        """对标题+正文合并做单次判断。"""
        return self.classify(f"{title}\n{text}")