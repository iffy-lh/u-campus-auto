"""
U校园听力自动化 - 答题策略引擎
负责：听力题类型判断、答案推理、多轮听力策略
"""

import asyncio
import re
import time
from typing import Optional
from dataclasses import dataclass, field

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    console = Console()
    RICH = True
except ImportError:
    RICH = False
    console = None


def log(msg: str, level: str = "info"):
    if RICH and console:
        styles = {
            "info": "cyan", "success": "green", "warn": "yellow",
            "error": "red", "step": "bold magenta",
        }
        console.log(f"[{styles.get(level, 'white')}]{msg}[/]")
    else:
        print(f"[{level.upper()}] {msg}")


@dataclass
class Question:
    """题目数据模型"""
    index: int
    type: str = "choice"        # choice, blank, true_false, match
    text: str = ""              # 题目文本 / 问题描述
    options: list[str] = field(default_factory=list)  # 选项列表
    options_text: list[str] = field(default_factory=list)  # 选项原始文本
    audio_played: bool = False  # 是否已播放对应音频
    transcript: str = ""        # 对应音频的转写文本
    answer: str = ""            # 最终选择的答案
    confidence: float = 0.0     # 置信度
    attempts: int = 0           # 尝试次数


@dataclass
class AnswerResult:
    """答题结果"""
    question_index: int
    selected_answer: str
    confidence: float
    strategy: str               # 使用的策略
    transcript_used: str = ""   # 使用的转录文本片段
    correct: Optional[bool] = None  # 是否正确（提交后才知道）


class QuestionClassifier:
    """题目类型分类器"""

    @staticmethod
    def classify(text: str, options: list) -> str:
        text_lower = text.lower()

        # 判断题
        if any(kw in text_lower for kw in ["true or false", "t/f", "对错", "判断", "yes or no", "是或否"]):
            return "true_false"

        # 匹配题
        if any(kw in text_lower for kw in ["match", "配对", "连线", "connect", "pair"]):
            return "match"

        # 填空题
        if "___" in text or "____" in text or "fill in" in text_lower or "空白" in text:
            return "blank"

        # 多选题
        if any(kw in text_lower for kw in ["choose two", "select all", "多选", "which two", "which three"]):
            return "multi_choice"

        # 有选项 = 选择题
        if options and len(options) > 0:
            return "choice"

        return "choice"  # 默认


class AnswerStrategy:
    """
    答题策略引擎
    U校园听力题的常见类型和对应的解题策略
    """

    def __init__(self):
        self.history: list[AnswerResult] = []

    # ── 策略1: 关键词匹配（选择题） ──

    def strategy_keyword_match(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略1：关键词直接匹配
        适用：细节题、事实题
        方法：计算每个选项与转录文本的关键词重叠度
        """
        from audio_engine import TranscriptAnalyzer

        if not question.options:
            return AnswerResult(
                question_index=question.index,
                selected_answer="",
                confidence=0,
                strategy="keyword_match",
            )

        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]
        best_idx, best_score, best_text = TranscriptAnalyzer.find_best_answer(
            transcript, opt_texts
        )

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=best_score,
            strategy="keyword_match",
            transcript_used=transcript[:200],
        )

    # ── 策略2: 否定排除法 ──

    def strategy_elimination(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略2：排除法
        适用：有明确否定词的题目
        方法：排除在转录中明确被否定的选项
        """
        from audio_engine import TranscriptAnalyzer

        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]

        # 标记被否定的选项
        negation_patterns = [
            r"not\s+(\w+)",
            r"no\s+(\w+)",
            r"never\s+(\w+)",
            r"don't\s+(\w+)",
            r"doesn't\s+(\w+)",
            r"didn't\s+(\w+)",
            r"isn't\s+(\w+)",
            r"aren't\s+(\w+)",
            r"wasn't\s+(\w+)",
            r"weren't\s+(\w+)",
            r"cannot\s+(\w+)",
            r"can't\s+(\w+)",
            r"won't\s+(\w+)",
            r"wouldn't\s+(\w+)",
            r"but\s+not\s+(\w+)",
            r"rather\s+than\s+(\w+)",
            r"instead\s+of\s+(\w+)",
        ]

        eliminated = set()
        transcript_lower = transcript.lower()

        for pattern in negation_patterns:
            matches = re.findall(pattern, transcript_lower)
            for match_word in matches:
                for i, opt in enumerate(opt_texts):
                    if match_word in opt.lower():
                        eliminated.add(i)
                        log(f"  排除选项 {chr(65+i)}: {opt} (被否定词排除)", "info")

        # 在未被排除的选项中选最佳
        remaining = [(i, opt_texts[i]) for i in range(len(opt_texts)) if i not in eliminated]

        if not remaining:
            # 全部被排除，回退到关键词匹配
            return self.strategy_keyword_match(question, transcript)

        # 在剩余选项中找最佳匹配
        remaining_texts = [opt for _, opt in remaining]
        best_local_idx, best_score, best_text = TranscriptAnalyzer.find_best_answer(
            transcript, remaining_texts
        )
        best_global_idx = remaining[best_local_idx][0]

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=best_score,
            strategy="elimination",
            transcript_used=transcript[:200],
        )

    # ── 策略3: 数字/时间匹配 ──

    def strategy_number_match(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略3：数字/时间匹配
        适用：包含数字、日期、时间的题目
        """
        from audio_engine import TranscriptAnalyzer

        question_text = question.text.lower()
        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]

        # 检查是否是数字题
        is_number_question = any(kw in question_text for kw in [
            "how many", "how much", "how old", "how long", "how far",
            "when", "what time", "what year", "what date", "which year",
            "多少", "什么时候", "哪一年", "多远", "多久",
        ])

        if not is_number_question:
            return self.strategy_keyword_match(question, transcript)

        # 提取转录中的数字
        numbers = TranscriptAnalyzer.extract_numbers(transcript)
        log(f"  转录中提取到的数字: {numbers}", "info")

        if not numbers:
            return self.strategy_keyword_match(question, transcript)

        # 匹配选项中含相同数字的
        best_idx = 0
        best_score = 0
        best_text = opt_texts[0]

        for i, opt in enumerate(opt_texts):
            opt_numbers = TranscriptAnalyzer.extract_numbers(opt)
            overlap = set(numbers) & set(opt_numbers)
            if overlap:
                # 数字匹配，高分
                return AnswerResult(
                    question_index=question.index,
                    selected_answer=opt,
                    confidence=0.9,
                    strategy="number_match",
                    transcript_used=", ".join(numbers[:5]),
                )
            else:
                # 回退到关键词匹配
                score = TranscriptAnalyzer.match_option(transcript, opt)
                if score > best_score:
                    best_score = score
                    best_idx = i
                    best_text = opt

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=best_score,
            strategy="number_match_fallback",
            transcript_used=", ".join(numbers[:5]),
        )

    # ── 策略4: 人名/地名匹配 ──

    def strategy_name_match(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略4：专有名词匹配
        适用：含人名、地名、专有名词的题目
        """
        from audio_engine import TranscriptAnalyzer

        question_text = question.text.lower()
        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]

        is_name_question = any(kw in question_text for kw in [
            "who", "whom", "whose", "which person", "name",
            "where", "which place", "location",
            "谁", "哪里", "什么地方",
        ])

        if not is_name_question:
            return self.strategy_keyword_match(question, transcript)

        # 提取专有名词
        names = TranscriptAnalyzer.extract_names(transcript)
        log(f"  转录中提取到的专有名词: {names[:10]}", "info")

        if not names:
            return self.strategy_keyword_match(question, transcript)

        # 匹配选项
        best_idx = 0
        best_score = 0
        best_text = opt_texts[0]

        for i, opt in enumerate(opt_texts):
            opt_lower = opt.lower()
            matched_names = [n for n in names if n.lower() in opt_lower]
            if matched_names:
                return AnswerResult(
                    question_index=question.index,
                    selected_answer=opt,
                    confidence=0.9,
                    strategy="name_match",
                    transcript_used=", ".join(matched_names),
                )
            else:
                score = TranscriptAnalyzer.match_option(transcript, opt)
                if score > best_score:
                    best_score = score
                    best_idx = i
                    best_text = opt

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=best_score,
            strategy="name_match_fallback",
            transcript_used=", ".join(names[:5]),
        )

    # ── 策略5: 推断题（主旨/意图） ──

    def strategy_inference(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略5：推断题策略
        适用：主旨题、推断题、态度题
        方法：关键词匹配 + 语义倾向分析
        """
        from audio_engine import TranscriptAnalyzer

        question_text = question.text.lower()
        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]

        # 判断是否是推断题
        is_inference = any(kw in question_text for kw in [
            "mainly", "main idea", "best title", "purpose", "attitude",
            "imply", "infer", "suggest", "probably", "might", "likely",
            "what can we learn", "what is the speaker",
            "主要", "目的", "态度", "推断", "标题",
        ])

        if not is_inference:
            return self.strategy_keyword_match(question, transcript)

        # 提取关键词（频率较高的实词）
        keywords = TranscriptAnalyzer.extract_keywords(transcript, top_k=15)
        log(f"  关键词: {keywords}", "info")

        # 对每个选项计算综合匹配分
        keyword_set = set(keywords)
        best_idx = 0
        best_score = -1
        best_text = opt_texts[0]

        for i, opt in enumerate(opt_texts):
            opt_words = set(re.findall(r'\b[a-zA-Z]+\b', opt.lower()))

            # 关键词覆盖度
            overlap = keyword_set & opt_words
            meaningful_overlap = overlap - {
                "the", "a", "an", "is", "are", "was", "were", "be",
                "have", "has", "had", "do", "does", "did", "will",
                "would", "could", "should", "may", "might", "can",
                "to", "of", "in", "for", "on", "with", "at", "by",
                "it", "this", "that", "not", "but", "and", "or",
                "i", "you", "he", "she", "we", "they", "about",
            }

            if opt_words:
                score = len(meaningful_overlap) / min(len(opt_words), 10)
            else:
                score = 0

            # 额外：精确短语匹配
            opt_lower = opt.lower()
            for kw in keywords[:5]:
                if kw.lower() in opt_lower:
                    score += 0.1

            if score > best_score:
                best_score = score
                best_idx = i
                best_text = opt

        confidence = min(best_score, 1.0)

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=confidence,
            strategy="inference",
            transcript_used=", ".join(keywords[:10]),
        )

    # ── 策略6: 对话场景推理 ──

    def strategy_conversation(self, question: Question, transcript: str) -> AnswerResult:
        """
        策略6：对话场景推理
        适用：对话类听力题
        """
        from audio_engine import TranscriptAnalyzer

        opt_texts = [o if isinstance(o, str) else str(o) for o in question.options]

        # 提取对话中的关键信息
        keywords = TranscriptAnalyzer.extract_keywords(transcript)
        names = TranscriptAnalyzer.extract_names(transcript)
        numbers = TranscriptAnalyzer.extract_numbers(transcript)
        dates = TranscriptAnalyzer.extract_dates(transcript)

        context = {
            "keywords": keywords[:10],
            "names": names[:5],
            "numbers": numbers[:5],
            "dates": dates[:3],
        }
        log(f"  对话上下文: {context}", "info")

        # 综合匹配所有上下文信息
        best_idx = 0
        best_score = -1
        best_text = opt_texts[0]

        for i, opt in enumerate(opt_texts):
            score = 0
            opt_lower = opt.lower()

            for kw in keywords[:10]:
                if kw.lower() in opt_lower:
                    score += 0.15
            for name in names[:5]:
                if name.lower() in opt_lower:
                    score += 0.2
            for num in numbers[:5]:
                if num in opt_lower:
                    score += 0.25
            for date in dates[:3]:
                if date.lower() in opt_lower:
                    score += 0.25

            if score > best_score:
                best_score = score
                best_idx = i
                best_text = opt

        confidence = min(best_score, 1.0)

        return AnswerResult(
            question_index=question.index,
            selected_answer=best_text,
            confidence=confidence,
            strategy="conversation",
        )

    # ── 综合答题入口 ──

    def answer_question(self, question: Question, transcript: str) -> AnswerResult:
        """
        综合答题入口：自动选择最佳策略
        """
        q_text = question.text.lower() if question.text else ""

        # 根据题目文本特征选择策略
        if any(kw in q_text for kw in ["how many", "how much", "when", "what time",
                                        "what year", "how old", "how long", "how far",
                                        "多少", "什么时候", "哪一年"]):
            result = self.strategy_number_match(question, transcript)

        elif any(kw in q_text for kw in ["who", "whom", "whose", "where", "which person",
                                          "谁", "哪里", "什么人"]):
            result = self.strategy_name_match(question, transcript)

        elif any(kw in q_text for kw in ["mainly", "main idea", "best title", "purpose",
                                          "attitude", "imply", "infer", "suggest",
                                          "主要", "目的", "态度", "推断"]):
            result = self.strategy_inference(question, transcript)

        elif any(kw in q_text for kw in ["conversation", "dialogue", "talk between",
                                          "对话", "交谈", "第.*段对话"]):
            result = self.strategy_conversation(question, transcript)

        elif any(kw in q_text for kw in ["not", "no", "never", "doesn't", "isn't",
                                          "didn't", "but not", "instead", "rather than"]):
            result = self.strategy_elimination(question, transcript)

        else:
            # 默认：关键词匹配
            result = self.strategy_keyword_match(question, transcript)

        self.history.append(result)
        return result

    def get_stats(self) -> dict:
        """获取答题统计"""
        total = len(self.history)
        if total == 0:
            return {"total": 0}

        avg_confidence = sum(r.confidence for r in self.history) / total
        strategies = {}
        for r in self.history:
            strategies[r.strategy] = strategies.get(r.strategy, 0) + 1

        return {
            "total": total,
            "avg_confidence": round(avg_confidence, 3),
            "strategies": strategies,
            "high_confidence": sum(1 for r in self.history if r.confidence >= 0.5),
            "low_confidence": sum(1 for r in self.history if r.confidence < 0.3),
        }
