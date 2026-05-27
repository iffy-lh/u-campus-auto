"""
U校园听力自动化 - 音频捕获与语音识别模块
负责：从浏览器捕获音频、录音、Whisper 转文字、关键词提取
"""

import asyncio
import io
import json
import hashlib
import re
import tempfile
import time
import wave
import os
from pathlib import Path
from typing import Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

try:
    import sounddevice as sd
    import soundfile as sf
    HAS_SOUND = True
except ImportError:
    HAS_SOUND = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False

try:
    from rich.console import Console
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


class AudioRecorder:
    """
    系统音频录制器
    从系统音频输出捕获声音（需要 PulseAudio / WASAPI loopback）
    """

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._recording = False
        self._buffer = []

    def start_recording(self):
        """开始录制"""
        if not HAS_SOUND:
            log("sounddevice 未安装，无法录制", "error")
            return False
        if not HAS_NUMPY:
            log("numpy 未安装，无法录制", "error")
            return False

        self._recording = True
        self._buffer = []

        def callback(indata, frames, time_info, status):
            if self._recording:
                self._buffer.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            callback=callback,
            dtype="float32",
        )
        self._stream.start()
        log("开始录音...", "info")
        return True

    def stop_recording(self) -> Optional[np.ndarray]:
        """停止录音并返回音频数据"""
        self._recording = False
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()

        if self._buffer:
            audio_data = np.concatenate(self._buffer, axis=0)
            log(f"录音完成，时长: {len(audio_data) / self.sample_rate:.1f}s", "success")
            return audio_data
        return None

    def save_to_file(self, audio_data: np.ndarray, filepath: str) -> str:
        """保存录音到文件"""
        sf.write(filepath, audio_data, self.sample_rate, format="WAV")
        log(f"音频已保存: {filepath}", "info")
        return filepath

    async def record_async(self, duration: float) -> Optional[np.ndarray]:
        """异步录制指定时长"""
        if not self.start_recording():
            return None
        await asyncio.sleep(duration)
        return self.stop_recording()


class AudioDownloader:
    """
    音频下载器
    从 URL 下载音频文件
    """

    @staticmethod
    def download(url: str, save_dir: str = "./audio_cache") -> Optional[str]:
        """下载音频文件到本地"""
        if not HAS_REQUESTS:
            log("requests 未安装，无法下载", "error")
            return None

        os.makedirs(save_dir, exist_ok=True)

        # 生成文件名
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        ext = AudioDownloader._guess_ext(url)
        filepath = os.path.join(save_dir, f"audio_{url_hash}{ext}")

        if os.path.exists(filepath):
            log(f"音频已缓存: {filepath}", "info")
            return filepath

        log(f"正在下载音频: {url[:80]}...", "info")
        try:
            resp = requests.get(url, timeout=30, stream=True, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://u.unipus.cn/",
            })
            resp.raise_for_status()

            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            log(f"下载完成: {filepath}", "success")
            return filepath
        except Exception as e:
            log(f"下载失败: {e}", "error")
            return None

    @staticmethod
    def _guess_ext(url: str) -> str:
        """猜测音频扩展名"""
        url_lower = url.lower()
        for ext in [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".wma"]:
            if ext in url_lower:
                return ext
        return ".mp3"


class WhisperRecognizer:
    """
    Whisper 语音识别器
    将音频转为文字
    """

    def __init__(self, model_size: str = "base", device: str = None, language: str = "en"):
        """
        model_size: tiny, base, small, medium, large
        language: en（英语）, zh（中文）, 等
        """
        if not HAS_WHISPER:
            log("whisper 未安装，请运行: pip install openai-whisper", "error")
            self.model = None
            return

        self.model_size = model_size
        self.language = language
        self.device = device or ("cuda" if self._check_cuda() else "cpu")
        self.model = None
        log(f"Whisper 配置: model={model_size}, device={self.device}, lang={language}", "info")

    @staticmethod
    def _check_cuda() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    def load_model(self):
        """加载 Whisper 模型"""
        if self.model is not None:
            return

        log(f"正在加载 Whisper {self.model_size} 模型...", "step")
        start = time.time()
        self.model = whisper.load_model(self.model_size).to(self.device)
        elapsed = time.time() - start
        log(f"模型加载完成，耗时 {elapsed:.1f}s", "success")

    def transcribe(self, audio_path: str) -> dict:
        """
        将音频文件转为文字
        返回: {"text": "...", "segments": [...], "language": "en"}
        """
        if not self.model:
            self.load_model()

        if not self.model:
            return {"text": "", "segments": [], "language": self.language}

        log(f"正在识别音频: {audio_path}", "info")
        start = time.time()

        result = self.model.transcribe(
            audio_path,
            language=self.language,
            fp16=(self.device == "cuda"),
            verbose=False,
        )

        elapsed = time.time() - start
        text = result.get("text", "").strip()
        log(f"识别完成 ({elapsed:.1f}s): {text[:100]}...", "success")

        return {
            "text": text,
            "segments": result.get("segments", []),
            "language": result.get("language", self.language),
        }

    def transcribe_array(self, audio_data: np.ndarray, sample_rate: int = 16000) -> dict:
        """从 numpy 数组识别语音"""
        if not self.model:
            self.load_model()

        if not self.model:
            return {"text": "", "segments": [], "language": self.language}

        # 确保格式正确
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)

        log("正在识别音频数据...", "info")
        result = self.model.transcribe(
            audio_data,
            language=self.language,
            fp16=(self.device == "cuda"),
            verbose=False,
        )

        return {
            "text": result.get("text", "").strip(),
            "segments": result.get("segments", []),
            "language": result.get("language", self.language),
        }

    def transcribe_with_timestamps(self, audio_path: str) -> list[dict]:
        """带时间戳的识别，返回每个句子的时间范围"""
        result = self.transcribe(audio_path)
        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": seg.get("text", "").strip(),
            })
        return segments


class AudioProcessor:
    """
    音频预处理器
    格式转换、降噪、裁剪等
    """

    @staticmethod
    def convert_to_wav(input_path: str, output_path: str = None, target_sr: int = 16000) -> str:
        """转换为 WAV 格式（Whisper 最佳格式）"""
        if not HAS_PYDUB:
            log("pydub 未安装，跳过格式转换", "warn")
            return input_path

        if not output_path:
            output_path = input_path.rsplit(".", 1)[0] + "_16k.wav"

        audio = AudioSegment.from_file(input_path)
        audio = audio.set_frame_rate(target_sr).set_channels(1).set_sample_width(2)
        audio.export(output_path, format="wav")
        log(f"音频已转换: {output_path}", "info")
        return output_path

    @staticmethod
    def normalize(audio_data) -> any:
        """音量归一化"""
        if not HAS_NUMPY:
            return audio_data
        import numpy as np
        audio_data = np.array(audio_data)
        max_val = np.max(np.abs(audio_data))
        if max_val > 0:
            return audio_data / max_val
        return audio_data

    @staticmethod
    def trim_silence(audio_data, threshold: float = 0.01) -> any:
        """去除首尾静音"""
        if not HAS_NUMPY:
            return audio_data
        import numpy as np
        audio_data = np.array(audio_data)
        indices = np.where(np.abs(audio_data) > threshold)[0]
        if len(indices) == 0:
            return audio_data
        return audio_data[indices[0]:indices[-1] + 1]

    @staticmethod
    def split_by_silence(audio_data, sample_rate: int = 16000,
                         min_silence_len: int = 500, silence_thresh: float = -40) -> list:
        """按静音分割音频为多个片段"""
        if not HAS_PYDUB or not HAS_NUMPY:
            return [audio_data]
        import numpy as np

        # 转为 pydub AudioSegment
        audio_bytes = (audio_data * 32767).astype(np.int16)
        segment = AudioSegment(
            audio_bytes.tobytes(),
            frame_rate=sample_rate,
            sample_width=2,
            channels=1,
        )

        chunks = AudioProcessor._split_on_silence_pydub(
            segment, min_silence_len=min_silence_len, silence_thresh=silence_thresh
        )
        return [np.array(c.get_array_of_samples(), dtype=np.float32) / 32767.0 for c in chunks]

    @staticmethod
    def _split_on_silence_pydub(audio_segment, min_silence_len=500, silence_thresh=-40):
        """pydub 的静音分割"""
        from pydub.silence import split_on_silence
        return split_on_silence(audio_segment, min_silence_len=min_silence_len, silence_thresh=silence_thresh)


class TranscriptAnalyzer:
    """
    转录文本分析器
    从识别文本中提取关键信息用于答题
    """

    @staticmethod
    def extract_keywords(text: str, top_k: int = 20) -> list[str]:
        """提取关键词"""
        # 去除停用词
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "here", "there", "when", "where", "why", "how", "all", "both",
            "each", "few", "more", "most", "other", "some", "such", "no", "nor",
            "not", "only", "own", "same", "so", "than", "too", "very", "just",
            "because", "but", "and", "or", "if", "while", "that", "this", "it",
            "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
            "she", "her", "they", "them", "their", "what", "which", "who",
            "whom", "these", "those", "am", "about", "up", "down",
        }

        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        word_freq = {}
        for w in words:
            if w not in stop_words and len(w) > 2:
                word_freq[w] = word_freq.get(w, 0) + 1

        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:top_k]]

    @staticmethod
    def extract_numbers(text: str) -> list[str]:
        """提取数字"""
        patterns = [
            r'\b\d+\.?\d*\b',                    # 普通数字
            r'\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b',
            r'\b(one|two|three|four|five|six|seven|eight|nine|ten)\b',
            r'\b(once|twice|three times)\b',
        ]
        numbers = []
        for p in patterns:
            numbers.extend(re.findall(p, text, re.IGNORECASE))
        return numbers

    @staticmethod
    def extract_names(text: str) -> list[str]:
        """提取人名（大写开头的单词，排除句首）"""
        sentences = re.split(r'[.!?]', text)
        names = []
        for sent in sentences:
            words = sent.strip().split()
            for i, word in enumerate(words):
                if i > 0 and word[0].isupper() and word.lower() not in {
                    "i", "the", "a", "an", "it", "this", "that", "my", "our",
                    "we", "they", "he", "she", "mr", "mrs", "ms", "dr",
                }:
                    clean = re.sub(r'[^a-zA-Z]', '', word)
                    if clean:
                        names.append(clean)
        return list(set(names))

    @staticmethod
    def extract_dates(text: str) -> list[str]:
        """提取日期"""
        patterns = [
            r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,?\s+\d{4})?\b',
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}',
            r'\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b',
            r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b',
            r'\b(?:today|yesterday|tomorrow|last\s+\w+|next\s+\w+)\b',
        ]
        dates = []
        for p in patterns:
            dates.extend(re.findall(p, text, re.IGNORECASE))
        return dates

    @staticmethod
    def match_option(transcript: str, option_text: str) -> float:
        """
        计算转录文本与选项的匹配度
        返回 0.0 ~ 1.0 的匹配分数
        """
        if not transcript or not option_text:
            return 0.0

        t_words = set(re.findall(r'\b[a-zA-Z]+\b', transcript.lower()))
        o_words = set(re.findall(r'\b[a-zA-Z]+\b', option_text.lower()))

        if not o_words:
            return 0.0

        # 直接包含
        option_lower = option_text.lower()
        transcript_lower = transcript.lower()
        if option_lower in transcript_lower:
            return 1.0

        # 关键词重叠
        overlap = t_words & o_words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                       "have", "has", "had", "do", "does", "did", "will", "would",
                       "could", "should", "may", "might", "shall", "can", "to", "of",
                       "in", "for", "on", "with", "at", "by", "from", "as", "into",
                       "it", "this", "that", "i", "you", "he", "she", "we", "they",
                       "not", "but", "and", "or", "if", "so", "very", "just", "about"}

        meaningful_overlap = overlap - stop_words
        meaningful_option = o_words - stop_words

        if not meaningful_option:
            return 0.0

        score = len(meaningful_overlap) / len(meaningful_option)

        # 额外加分：连续词组匹配
        o_bigrams = set()
        o_word_list = list(o_words)
        for i in range(len(o_word_list) - 1):
            o_bigrams.add(f"{o_word_list[i]} {o_word_list[i+1]}")

        t_word_list = list(t_words)
        t_bigrams = set()
        for i in range(len(t_word_list) - 1):
            t_bigrams.add(f"{t_word_list[i]} {t_word_list[i+1]}")

        bigram_overlap = t_bigrams & o_bigrams
        if o_bigrams:
            score += len(bigram_overlap) / len(o_bigrams) * 0.5

        return min(score, 1.0)

    @staticmethod
    def find_best_answer(transcript: str, options: list[str]) -> tuple[int, float, str]:
        """
        从多个选项中找到最匹配的答案
        返回: (最佳选项索引, 匹配分数, 最佳选项文本)
        """
        best_idx = 0
        best_score = -1
        best_text = ""

        for i, opt in enumerate(options):
            score = TranscriptAnalyzer.match_option(transcript, opt)
            if score > best_score:
                best_score = score
                best_idx = i
                best_text = opt

        return best_idx, best_score, best_text

    @staticmethod
    def extract_fill_answer(transcript: str, question_text: str = "") -> str:
        """
        从转录文本中提取填空题答案
        策略：找问题中空白处前后的关键词，在转录中定位
        """
        # 如果问题中有 "___" 或 "____" 标记
        if "___" in question_text or "____" in question_text:
            # 提取空白前后的关键词
            parts = re.split(r"_{3,}", question_text)
            if len(parts) >= 2:
                before = parts[0].strip().split()[-3:]  # 空白前3个词
                after = parts[1].strip().split()[:3]    # 空白后3个词

                before_str = " ".join(before).lower()
                after_str = " ".join(after).lower()
                transcript_lower = transcript.lower()

                # 在转录中定位
                before_pos = transcript_lower.find(before_str)
                after_pos = transcript_lower.find(after_str)

                if before_pos >= 0 and after_pos >= 0 and after_pos > before_pos:
                    start = before_pos + len(before_str)
                    answer = transcript[start:after_pos].strip()
                    # 清理
                    answer = re.sub(r'^[,.\s]+|[,.\s]+$', '', answer)
                    if answer:
                        return answer

        # 默认返回转录中最长的名词短语
        words = transcript.split()
        if words:
            # 简单策略：返回第一个有意义的词
            for w in words:
                clean = re.sub(r'[^a-zA-Z0-9]', '', w)
                if len(clean) > 2:
                    return clean

        return transcript.strip().split()[0] if transcript.strip() else ""
