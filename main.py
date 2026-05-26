"""
U校园听力自动化 - 主程序
整合所有模块，提供完整的自动化流程
"""

import asyncio
import json
import os
import sys
import time
import argparse
from pathlib import Path
from dataclasses import dataclass, field

from browser_engine import BrowserEngine, SELECTORS, console, log, Table
from audio_engine import (
    AudioRecorder, AudioDownloader, WhisperRecognizer,
    AudioProcessor, TranscriptAnalyzer,
    HAS_WHISPER, HAS_SOUND, HAS_REQUESTS, HAS_PYDUB, RICH,
)
from answer_engine import QuestionClassifier, AnswerStrategy, Question, AnswerResult


@dataclass
class Config:
    """运行配置"""
    # 账号
    username: str = ""
    password: str = ""
    login_url: str = "https://u.unipus.cn/user/login"

    # 浏览器
    headless: bool = False
    slow_mo: int = 100
    user_data_dir: str = "./browser_data"

    # Whisper
    whisper_model: str = "base"
    whisper_language: str = "en"
    whisper_device: str = ""  # auto

    # 音频
    audio_cache_dir: str = "./audio_cache"
    use_system_recorder: bool = True  # 使用系统录音
    max_listen_rounds: int = 3   # 最多听几遍音频

    # 答题
    auto_submit: bool = True
    min_confidence: float = 0.15  # 最低置信度，低于此值则随机猜
    delay_between_questions: float = 1.0

    # 课程导航
    target_course: str = ""  # 留空则选第一个
    target_unit: str = ""    # 留空则选第一个

    # 调试
    debug: bool = False
    screenshot_on_error: bool = True


class UCampusAutomator:
    """
    U校园听力自动化主控制器
    整合浏览器控制、音频识别、答题策略
    """

    def __init__(self, config: Config):
        self.config = config
        self.browser = BrowserEngine(
            headless=config.headless,
            slow_mo=config.slow_mo,
            user_data_dir=config.user_data_dir if not config.headless else None,
        )
        self.recognizer = WhisperRecognizer(
            model_size=config.whisper_model,
            language=config.whisper_language,
            device=config.whisper_device or None,
        )
        self.recorder = AudioRecorder() if HAS_SOUND else None
        self.strategy = AnswerStrategy()
        self.results: list[AnswerResult] = []

    async def run(self):
        """主流程"""
        try:
            await self._show_banner()
            await self._check_deps()
            await self.browser.start()

            # 第1步：登录
            logged_in = await self._login()
            if not logged_in:
                log("登录失败，退出", "error")
                return

            # 第2步：导航到课程
            course_ok = await self._navigate_course()
            if not course_ok:
                log("课程导航失败，退出", "error")
                return

            # 第3步：导航到单元
            unit_ok = await self._navigate_unit()
            if not unit_ok:
                log("单元导航失败，退出", "error")
                return

            # 第4步：开始听力任务
            await self._do_listening_tasks()

        except KeyboardInterrupt:
            log("\n用户中断，安全退出...", "warn")
        except Exception as e:
            log(f"运行时错误: {e}", "error")
            if self.config.debug:
                import traceback
                traceback.print_exc()
            if self.config.screenshot_on_error:
                await self.browser.screenshot("error_screenshot.png")
        finally:
            await self.browser.stop()
            self._print_final_report()

    async def _show_banner(self):
        banner = r"""
╔══════════════════════════════════════════════╗
║         U校园听力自动化 v1.0                  ║
║         U Campus Listening Auto              ║
╚══════════════════════════════════════════════╝
        """
        if RICH and console:
            console.print(banner, style="bold cyan")
        else:
            print(banner)

    async def _check_deps(self):
        """检查依赖"""
        deps = [
            ("Playwright", True),  # 必须有
            ("Whisper", HAS_WHISPER),
            ("SoundDevice", HAS_SOUND),
            ("Requests", HAS_REQUESTS),
            ("PyDub", HAS_PYDUB),
        ]
        log("依赖检查:", "step")
        for name, available in deps:
            status = "OK" if available else "MISSING"
            level = "success" if available else "warn"
            log(f"  {name}: {status}", level)

        if not HAS_WHISPER:
            log("Whisper 未安装！请运行: pip install openai-whisper", "error")
            log("同时也需要安装 ffmpeg: sudo apt install ffmpeg", "error")

    async def _login(self) -> bool:
        """登录流程"""
        log("═══ 第1步：登录 ═══", "step")

        # 尝试恢复已保存的登录态
        if os.path.exists(self.config.user_data_dir):
            log("检测到已保存的浏览器数据，尝试复用登录态...", "info")

        # 检查是否有配置账号
        if not self.config.username:
            self.config.username = input("请输入学号/账号: ").strip()
        if not self.config.password:
            self.config.password = input("请输入密码: ").strip()

        if not self.config.username or not self.config.password:
            log("未提供账号密码，请手动在浏览器中登录", "warn")
            await self.browser.goto(self.config.login_url)
            log("请在浏览器中手动登录，完成后按回车继续...", "warn")
            input()
            return True

        return await self.browser.login(
            username=self.config.username,
            password=self.config.password,
            login_url=self.config.login_url,
            captcha_callback=self._handle_captcha,
        )

    async def _handle_captcha(self, page):
        """处理验证码：截图让用户输入"""
        log("需要验证码！", "warn")
        captcha_path = "captcha.png"
        captcha_img = await self.browser.find_element(SELECTORS["login"]["captcha_img"], timeout=5000)
        if captcha_img:
            await captcha_img.screenshot(path=captcha_path)
            if RICH and console:
                console.print(f"验证码已截图: {captcha_path}", style="yellow")
            code = input("请输入验证码: ").strip()
            return code
        return None

    async def _navigate_course(self) -> bool:
        """导航到课程"""
        log("═══ 第2步：选择课程 ═══", "step")
        return await self.browser.navigate_to_course(self.config.target_course)

    async def _navigate_unit(self) -> bool:
        """导航到单元"""
        log("═══ 第3步：选择单元 ═══", "step")
        return await self.browser.navigate_to_unit(self.config.target_unit)

    async def _do_listening_tasks(self):
        """执行听力任务（核心流程）"""
        log("═══ 第4步：开始听力答题 ═══", "step")

        # 等待页面完全加载
        await asyncio.sleep(3)

        # 打印页面信息用于调试
        if self.config.debug:
            await self.browser.dump_page_info()

        # 扫描当前页面所有题目
        questions = await self._scan_questions()
        if not questions:
            log("未找到题目，尝试点击开始按钮...", "warn")
            started = await self.browser.click_any(SELECTORS["learning"]["start_btn"])
            if started:
                await asyncio.sleep(5)
                questions = await self._scan_questions()

        if not questions:
            log("仍然未找到题目，请检查页面", "error")
            await self.browser.screenshot("no_questions.png")
            if self.config.debug:
                html = await self.browser.get_page_html()
                with open("page_debug.html", "w", encoding="utf-8") as f:
                    f.write(html)
                log("已保存页面HTML到 page_debug.html", "info")
            return

        log(f"共找到 {len(questions)} 道题目，开始处理...", "step")

        # 加载 Whisper 模型
        if HAS_WHISPER:
            self.recognizer.load_model()

        # 逐题处理
        for i, q in enumerate(questions):
            log(f"\n── 第 {i+1}/{len(questions)} 题 ──", "step")
            result = await self._process_single_question(q)
            self.results.append(result)
            await asyncio.sleep(self.config.delay_between_questions)

        # 提交答案
        if self.config.auto_submit:
            log("\n正在提交答案...", "step")
            await self.browser.submit_answer()
            await asyncio.sleep(3)

            # 获取成绩
            score = await self.browser.get_score()
            if score:
                log(f"得分: {score}", "success")

            # 获取正确答案
            correct_answers = await self.browser.get_correct_answers()
            if correct_answers:
                log("正确答案:", "info")
                for j, ca in enumerate(correct_answers):
                    if j < len(self.results):
                        self.results[j].correct = (
                            self.results[j].selected_answer.lower()
                            in ca.get("correct", "").lower()
                        )
                        if self.results[j].correct:
                            log(f"  第{j+1}题: ✓ {ca.get('correct', '')}", "success")
                        else:
                            log(
                                f"  第{j+1}题: ✗ "
                                f"(你的: {self.results[j].selected_answer}, "
                                f"正确: {ca.get('correct', '')})",
                                "error",
                            )

    async def _scan_questions(self) -> list[Question]:
        """扫描页面上的所有题目"""
        raw_questions = await self.browser.get_questions()
        questions = []

        for i, rq in enumerate(raw_questions):
            q = Question(
                index=i,
                type=QuestionClassifier.classify(rq["text"], rq["options"]),
                text=rq["text"],
                options=[opt["text"] for opt in rq["options"]],
            )
            questions.append(q)
            log(f"  题目{i+1}: [{q.type}] {q.text[:60]}...", "info")
            for j, opt in enumerate(q.options):
                log(f"    {chr(65+j)}. {opt}", "info")

        return questions

    async def _process_single_question(self, question: Question) -> AnswerResult:
        """处理单道题目"""
        # 1. 获取音频（最多听 max_listen_rounds 遍）
        best_transcript = ""
        best_audio_path = None

        for round_num in range(1, self.config.max_listen_rounds + 1):
            log(f"  第 {round_num} 遍听音频...", "info")

            # 捕获音频
            audio_path = await self._capture_audio(round_num)
            if audio_path:
                best_audio_path = audio_path

            # 识别音频
            if best_audio_path and HAS_WHISPER:
                result = self.recognizer.transcribe(best_audio_path)
                transcript = result.get("text", "")

                if transcript:
                    best_transcript += " " + transcript
                    log(f"  识别结果: {transcript[:80]}...", "info")

                    # 关键词提取
                    keywords = TranscriptAnalyzer.extract_keywords(transcript, top_k=10)
                    log(f"  关键词: {keywords}", "info")

                    # 如果识别质量足够好，不需要继续听
                    if len(transcript.split()) > 5:
                        break

        # 2. 使用策略引擎答题
        if best_transcript.strip():
            result = self.strategy.answer_question(question, best_transcript)
            log(
                f"  策略: {result.strategy}, "
                f"答案: {result.selected_answer}, "
                f"置信度: {result.confidence:.2f}",
                "info",
            )
        else:
            # 没有识别出文本，使用随机策略
            import random
            if question.options:
                rand_idx = random.randint(0, len(question.options) - 1)
                result = AnswerResult(
                    question_index=question.index,
                    selected_answer=question.options[rand_idx],
                    confidence=0.1,
                    strategy="random_guess",
                )
            else:
                result = AnswerResult(
                    question_index=question.index,
                    selected_answer="",
                    confidence=0,
                    strategy="skip",
                )
            log(f"  无法识别音频，随机选择: {result.selected_answer}", "warn")

        # 3. 在页面上选择答案
        await self._select_answer_on_page(question, result)

        return result

    async def _capture_audio(self, round_num: int) -> Optional[str]:
        """
        捕获音频
        优先顺序：下载音频URL > 系统录音 > 浏览器内录制
        """
        # 方法1: 尝试获取音频源 URL 并下载
        audio_src = await self.browser.get_audio_src()
        if audio_src and HAS_REQUESTS:
            audio_path = AudioDownloader.download(
                audio_src, save_dir=self.config.audio_cache_dir
            )
            if audio_path:
                return audio_path

        # 方法2: 系统录音
        if self.config.use_system_recorder and self.recorder:
            # 先播放音频
            await self.browser.play_audio()
            log("  正在录音...", "info")

            # 录制
            duration = 60  # 最多录60秒
            audio_data = await self.recorder.record_async(duration)

            if audio_data is not None:
                # 保存
                save_path = os.path.join(
                    self.config.audio_cache_dir,
                    f"round_{round_num}_{int(time.time())}.wav",
                )
                os.makedirs(self.config.audio_cache_dir, exist_ok=True)
                self.recorder.save_to_file(audio_data, save_path)
                return save_path

            await self.browser.pause_audio()

        # 方法3: 通过 CDP 捕获（高级方法）
        audio_path = await self._capture_via_cdp(round_num)
        if audio_path:
            return audio_path

        return None

    async def _capture_via_cdp(self, round_num: int) -> Optional[str]:
        """
        通过 Chrome DevTools Protocol 从浏览器中捕获音频数据
        使用 Page.captureScreenshot + AudioBuffer 技术
        """
        page = self.browser.page
        if not page:
            return None

        try:
            # 启动音频上下文
            audio_data = await page.evaluate("""
                async () => {
                    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    const mediaElement = document.querySelector('audio') || document.querySelector('video');
                    if (!mediaElement) return null;

                    const source = audioCtx.createMediaElementSource(mediaElement);
                    const recorder = new MediaRecorder(
                        new MediaStream([audioCtx.createMediaStreamDestination().stream])
                    );

                    return { status: 'found', tag: mediaElement.tagName };
                }
            """)
            if not audio_data:
                return None

        except Exception:
            pass

        return None

    async def _select_answer_on_page(self, question: Question, result: AnswerResult):
        """在页面上选择答案"""
        page = self.browser.page

        if not result.selected_answer:
            log("  没有答案可选，跳过", "warn")
            return

        # 尝试在选项中找到匹配的
        option_elements = await self.browser.find_all(SELECTORS["question"]["options"])

        if option_elements:
            for i, opt_el in enumerate(option_elements):
                opt_text = (await opt_el.inner_text()).strip()
                if (result.selected_answer.lower() in opt_text.lower() or
                        opt_text.lower() in result.selected_answer.lower()):
                    await opt_el.click()
                    log(f"  已选择: {opt_text}", "success")
                    return

            # 如果没找到精确匹配，尝试按索引
            if result.question_index < len(option_elements):
                # 根据答案文本估算索引
                answer_lower = result.selected_answer.lower()
                for i, opt_el in enumerate(option_elements):
                    opt_text = (await opt_el.inner_text()).strip()
                    score = TranscriptAnalyzer.match_option(answer_lower, opt_text)
                    if score > 0.5:
                        await opt_el.click()
                        log(f"  已选择(模糊匹配): {opt_text}", "success")
                        return

            # 最后手段：选第一个
            if option_elements:
                await option_elements[0].click()
                log("  已选择: 第一选项（fallback）", "warn")

        # 填空题
        blank_inputs = await self.browser.find_all(SELECTORS["question"]["blank_input"])
        if blank_inputs:
            await blank_inputs[0].fill(result.selected_answer)
            log(f"  已填写: {result.selected_answer}", "success")

    def _print_final_report(self):
        """打印最终报告"""
        log("\n" + "=" * 50, "step")
        log("最终答题报告", "step")
        log("=" * 50, "step")

        if RICH and console:
            table = Table(title="答题详情")
            table.add_column("题号", style="cyan")
            table.add_column("策略", style="magenta")
            table.add_column("选择答案", style="green")
            table.add_column("置信度", style="yellow")
            table.add_column("正确", style="bold")

            for i, r in enumerate(self.results):
                correct_mark = ""
                if r.correct is True:
                    correct_mark = "✓"
                elif r.correct is False:
                    correct_mark = "✗"
                else:
                    correct_mark = "?"

                table.add_row(
                    str(i + 1),
                    r.strategy,
                    r.selected_answer[:40] if r.selected_answer else "-",
                    f"{r.confidence:.2f}",
                    correct_mark,
                )

            console.print(table)
        else:
            for i, r in enumerate(self.results):
                correct_mark = "?" if r.correct is None else ("✓" if r.correct else "✗")
                print(f"  题{i+1}: [{r.strategy}] {r.selected_answer} (置信度: {r.confidence:.2f}) [{correct_mark}]")

        # 统计
        stats = self.strategy.get_stats()
        log(f"\n总题数: {stats.get('total', 0)}", "info")
        log(f"平均置信度: {stats.get('avg_confidence', 0):.3f}", "info")
        log(f"高置信度(≥0.5): {stats.get('high_confidence', 0)}", "info")
        log(f"低置信度(<0.3): {stats.get('low_confidence', 0)}", "info")

        if stats.get("strategies"):
            log("策略分布:", "info")
            for strategy, count in stats["strategies"].items():
                log(f"  {strategy}: {count}", "info")

        correct_count = sum(1 for r in self.results if r.correct is True)
        wrong_count = sum(1 for r in self.results if r.correct is False)
        unknown_count = sum(1 for r in self.results if r.correct is None)

        total_graded = correct_count + wrong_count
        if total_graded > 0:
            accuracy = correct_count / total_graded * 100
            log(f"\n正确率: {correct_count}/{total_graded} = {accuracy:.1f}%", "bold")


def load_config_from_file(path: str) -> Config:
    """从配置文件加载"""
    config = Config()
    if not os.path.exists(path):
        return config

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for key, value in data.items():
        if hasattr(config, key):
            setattr(config, key, value)

    return config


def save_config_template(path: str = "config.json"):
    """生成配置模板"""
    template = {
        "username": "你的学号",
        "password": "你的密码",
        "login_url": "https://u.unipus.cn/user/login",
        "headless": False,
        "slow_mo": 100,
        "whisper_model": "base",
        "whisper_language": "en",
        "audio_cache_dir": "./audio_cache",
        "use_system_recorder": True,
        "max_listen_rounds": 3,
        "auto_submit": True,
        "min_confidence": 0.15,
        "target_course": "",
        "target_unit": "",
        "debug": False,
        "screenshot_on_error": True,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
    print(f"配置模板已生成: {path}")


async def main():
    parser = argparse.ArgumentParser(description="U校园听力自动化工具")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径")
    parser.add_argument("--init", action="store_true", help="生成配置模板")
    parser.add_argument("-u", "--username", help="学号/账号")
    parser.add_argument("-p", "--password", help="密码")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--model", default="base", help="Whisper 模型大小")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument("--course", help="目标课程名")
    parser.add_argument("--unit", help="目标单元名")

    args = parser.parse_args()

    if args.init:
        save_config_template(args.config)
        return

    # 加载配置
    config = load_config_from_file(args.config)

    # 命令行参数覆盖配置文件
    if args.username:
        config.username = args.username
    if args.password:
        config.password = args.password
    if args.headless:
        config.headless = True
    if args.model:
        config.whisper_model = args.model
    if args.debug:
        config.debug = True
    if args.course:
        config.target_course = args.course
    if args.unit:
        config.target_unit = args.unit

    # 运行
    automator = UCampusAutomator(config)
    await automator.run()


if __name__ == "__main__":
    asyncio.run(main())
