"""
U校园听力自动化 - 浏览器控制引擎
负责：登录、导航、DOM 操作、音频元素捕获、答题交互
"""

import asyncio
import json
import time
import re
from pathlib import Path
from typing import Optional, Callable, Any

from playwright.async_api import (
    async_playwright,
    Page,
    Browser,
    BrowserContext,
    ElementHandle,
    TimeoutError as PWTimeout,
)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
    RICH = True
except ImportError:
    RICH = False
    console = None


def log(msg: str, level: str = "info"):
    """统一日志输出"""
    if RICH and console:
        styles = {
            "info": "cyan",
            "success": "green",
            "warn": "yellow",
            "error": "red",
            "step": "bold magenta",
        }
        console.log(f"[{styles.get(level, 'white')}]{msg}[/]")
    else:
        print(f"[{level.upper()}] {msg}")


# ──────────────────────────────────────────────
# 选择器配置 — 覆盖 U 校园常见 DOM 结构
# ──────────────────────────────────────────────
# U校园（unipus.cn）的页面结构经历了多次改版，以下选择器集合
# 覆盖了目前已知的各个版本。

SELECTORS = {
    # ── 登录页面 ──
    "login": {
        "username_input": [
            "input[name='username']",
            "input#username",
            "input[placeholder*='学号']",
            "input[placeholder*='账号']",
            "input[type='text'].el-input__inner",
        ],
        "password_input": [
            "input[name='password']",
            "input#password",
            "input[type='password']",
            "input[placeholder*='密码']",
        ],
        "login_btn": [
            "button[type='submit']",
            "button.login-btn",
            "button:has-text('登录')",
            "a:has-text('登录')",
            ".el-button--primary",
            "button.el-button.el-button--primary",
            "button[type='button']:has-text('登录')",
            "span:has-text('登录')",
            "div.login-btn",
            "[class*='login'] button",
            "[class*='Login'] button",
        ],
        "captcha_img": [
            "img.captcha",
            "img.verify-code",
            "#captcha-img",
            "img[alt*='验证码']",
        ],
        "captcha_input": [
            "input[name='captcha']",
            "input[placeholder*='验证码']",
            "#captcha-input",
        ],
    },

    # ── 课程首页 / 课程列表 ──
    "course": {
        "course_card": [
            ".course-card",
            ".course-item",
            ".course-list .item",
            "[class*='course']",
        ],
        "course_title": [
            ".course-name",
            ".course-title",
            "h3",
            ".title",
        ],
        "enter_course_btn": [
            "a:has-text('进入')",
            "button:has-text('进入课程')",
            ".enter-btn",
        ],
    },

    # ── 课程学习页面 ──
    "learning": {
        "unit_list": [
            ".unit-list .unit-item",
            ".chapter-list .chapter-item",
            "[data-unit-id]",
            ".catalog-item",
        ],
        "unit_title": [
            ".unit-title",
            ".chapter-title",
            "h4",
        ],
        "start_btn": [
            "button:has-text('开始学习')",
            "button:has-text('开始')",
            "a:has-text('开始')",
            ".start-btn",
            ".study-btn",
        ],
        "learn_tab": [
            "a:has-text('学习')",
            "div:has-text('学习')",
            "[class*='tab']:has-text('学习')",
        ],
    },

    # ── 听力播放区域 ──
    "audio": {
        "player": [
            "audio",
            "audio-player",
            "[class*='audio-player']",
            "[class*='audioPlayer']",
            "#audio-player",
            "video",
        ],
        "play_btn": [
            "button.play",
            ".play-btn",
            ".audio-play",
            "[class*='play']",
            "button:has-text('播放')",
            ".el-icon-video-play",
        ],
        "pause_btn": [
            "button.pause",
            ".pause-btn",
            "[class*='pause']",
        ],
        "progress_bar": [
            ".progress-bar",
            ".audio-progress",
            "[class*='progress']",
            "input[type='range']",
        ],
        "speed_btn": [
            ".speed-btn",
            "[class*='speed']",
            "button:has-text('倍速')",
        ],
        "current_time": [
            ".current-time",
            ".time-now",
            "[class*='current']",
        ],
        "total_time": [
            ".total-time",
            ".time-duration",
            "[class*='duration']",
        ],
    },

    # ── 题目区域 ──
    "question": {
        "container": [
            ".question-list",
            ".question-item",
            "[class*='question']",
            ".exercise-item",
            ".test-item",
            ".task-item",
        ],
        "question_text": [
            ".question-text",
            ".question-content",
            ".stem",
            "[class*='stem']",
            ".title",
        ],
        "options": [
            ".option",
            ".answer-option",
            "[class*='option']",
            "input[type='radio']",
            "input[type='checkbox']",
            ".choice",
        ],
        "option_text": [
            ".option-text",
            ".option-content",
            "label",
            "span.option",
        ],
        "blank_input": [
            "input.blank",
            "input.fill-blank",
            "[class*='blank'] input",
            "input[type='text'][class*='answer']",
        ],
        "submit_btn": [
            "button:has-text('提交')",
            "button:has-text('Submit')",
            ".submit-btn",
            "button.submit",
        ],
        "next_btn": [
            "button:has-text('下一题')",
            "button:has-text('Next')",
            ".next-btn",
            "button:has-text('继续')",
        ],
        "prev_btn": [
            "button:has-text('上一题')",
            "button:has-text('Previous')",
        ],
        "question_number": [
            ".question-num",
            ".question-index",
            "[class*='number']",
        ],
    },

    # ── 结果/反馈 ──
    "result": {
        "score": [
            ".score",
            ".result-score",
            "[class*='score']",
        ],
        "correct_answer": [
            ".correct-answer",
            ".right-answer",
            "[class*='correct']",
            ".answer-analysis",
        ],
        "explanation": [
            ".explanation",
            ".analysis",
            "[class*='analysis']",
            ".answer-explain",
        ],
        "retry_btn": [
            "button:has-text('重试')",
            "button:has-text('重新')",
            ".retry-btn",
        ],
    },
}


class BrowserEngine:
    """
    Playwright 浏览器控制引擎
    负责所有浏览器层面的操作
    """

    def __init__(
        self,
        headless: bool = False,
        slow_mo: int = 100,
        viewport: dict = None,
        user_data_dir: str = None,
        chromium_path: str = None,
    ):
        self.headless = headless
        self.slow_mo = slow_mo
        self.viewport = viewport or {"width": 1280, "height": 900}
        self.user_data_dir = user_data_dir
        self._chromium_path = chromium_path

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self):
        """启动浏览器"""
        log("正在启动浏览器...", "step")
        self._playwright = await async_playwright().start()

        launch_opts = {
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        }

        if self._chromium_path:
            launch_opts["executable_path"] = self._chromium_path


        if self.user_data_dir:
            # 使用持久化上下文保存登录态
            self._context = await self._playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                **launch_opts,
                viewport=self.viewport,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            self._browser = self._context.browser
        else:
            self._browser = await self._playwright.chromium.launch(**launch_opts)
            self._context = await self._browser.new_context(
                viewport=self.viewport,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

        self._page = await self._context.new_page()

        # 注入反检测脚本
        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)

        log("浏览器启动成功", "success")

    async def stop(self):
        """关闭浏览器"""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log("浏览器已关闭", "info")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("浏览器未启动，请先调用 start()")
        return self._page

    # ── 导航 ──

    async def goto(self, url: str, wait_until: str = "networkidle"):
        """导航到 URL"""
        log(f"正在访问: {url}", "info")
        await self.page.goto(url, wait_until=wait_until, timeout=60000)
        log(f"页面加载完成: {await self.page.title()}", "success")

    async def wait_and_reload(self, selector: str, timeout: int = 30000):
        """等待元素出现，超时则刷新重试"""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except PWTimeout:
            log(f"等待超时，刷新重试: {selector}", "warn")
            await self.page.reload(wait_until="networkidle")
            try:
                await self.page.wait_for_selector(selector, timeout=timeout)
                return True
            except PWTimeout:
                return False

    # ── 智能元素查找 ──

    async def find_element(self, selectors: list[str], timeout: int = 10000) -> Optional[ElementHandle]:
        """尝试多个选择器，返回第一个匹配的元素"""
        for sel in selectors:
            try:
                el = await self.page.wait_for_selector(sel, timeout=min(timeout, 3000))
                if el:
                    return el
            except Exception:
                continue
        return None

    async def find_all(self, selectors: list[str]) -> list[ElementHandle]:
        """尝试多个选择器，返回所有匹配元素"""
        for sel in selectors:
            els = await self.page.query_selector_all(sel)
            if els:
                return els
        return []

    async def click_any(self, selectors: list[str], timeout: int = 10000) -> bool:
        """尝试点击任意一个匹配的选择器"""
        el = await self.find_element(selectors, timeout)
        if el:
            await el.click()
            return True
        log(f"未找到可点击元素: {selectors}", "warn")
        return False

    async def fill_any(self, selectors: list[str], text: str, timeout: int = 10000) -> bool:
        """尝试向任意一个匹配的选择器填入文本"""
        el = await self.find_element(selectors, timeout)
        if el:
            await el.fill(text)
            return True
        log(f"未找到可填写元素: {selectors}", "warn")
        return False

    async def get_text_any(self, selectors: list[str]) -> Optional[str]:
        """尝试获取任意一个匹配选择器的文本"""
        el = await self.find_element(selectors)
        if el:
            return await el.inner_text()
        return None

    # ── 登录流程 ──

    async def login(
        self,
        username: str,
        password: str,
        login_url: str = "https://u.unipus.cn/user/login",
        captcha_callback: Optional[Callable] = None,
    ) -> bool:
        """
        登录 U 校园
        captcha_callback: 如果需要验证码，调用此回调让用户手动输入
        """
        log("开始登录流程...", "step")
        await self.goto(login_url)
        await asyncio.sleep(2)

        # 填写用户名
        if not await self.fill_any(SELECTORS["login"]["username_input"], username):
            log("未找到用户名输入框", "error")
            return False
        log("已填写用户名", "info")

        # 填写密码
        if not await self.fill_any(SELECTORS["login"]["password_input"], password):
            log("未找到密码输入框", "error")
            return False
        log("已填写密码", "info")

        # 检查验证码
        captcha_img = await self.find_element(SELECTORS["login"]["captcha_img"], timeout=3000)
        if captcha_img:
            log("检测到验证码", "warn")
            if captcha_callback:
                captcha_code = await captcha_callback(self.page)
                if captcha_code:
                    await self.fill_any(SELECTORS["login"]["captcha_input"], captcha_code)
            else:
                log("等待手动输入验证码...", "warn")
                await asyncio.sleep(15)

        # 尝试点击登录按钮
        clicked = await self.click_any(SELECTORS["login"]["login_btn"])

        # 如果没找到按钮，尝试按 Enter 提交表单
        if not clicked:
            log("未找到登录按钮，尝试按 Enter 提交...", "warn")
            await self.page.keyboard.press("Enter")

        # 等待页面跳转
        await asyncio.sleep(5)

        # 检查是否登录成功（多重验证）
        current_url = self.page.url

        # 情况1: 跳转到首页
        if "index.html" in current_url or "home" in current_url.lower():
            if "logout" in current_url.lower():
                log(f"检测到 logout 参数，尝试清除后重新访问...", "warn")
                # 直接跳转到课程页面
                await self.goto("https://u.unipus.cn/student/course", wait_until="domcontentloaded")
                await asyncio.sleep(3)
                if "login" not in self.page.url.lower():
                    log("登录成功！", "success")
                    return True

            log(f"登录成功！当前页面: {current_url}", "success")
            return True

        # 情况2: 跳转到课程或其他页面
        if "login" not in current_url.lower():
            log(f"登录成功！当前页面: {current_url}", "success")
            return True

        # 情况3: 还在登录页，检查错误提示
        error_el = await self.page.query_selector(".error-message, .el-form-item__error, [class*='error'], .el-message--error")
        if error_el:
            error_text = await error_el.inner_text()
            log(f"登录失败: {error_text}", "error")
        else:
            log("登录可能失败，请检查页面", "error")
        return False

    # ── 课程导航 ──

    async def navigate_to_course(self, course_name: str = None) -> bool:
        """导航到指定课程"""
        log("正在查找课程...", "step")

        # 等待课程列表加载
        await asyncio.sleep(2)
        cards = await self.find_all(SELECTORS["course"]["course_card"])

        if not cards:
            log("未找到课程列表，尝试刷新...", "warn")
            await self.page.reload(wait_until="networkidle")
            await asyncio.sleep(3)
            cards = await self.find_all(SELECTORS["course"]["course_card"])

        if not cards:
            log("仍未找到课程，请检查是否已选课", "error")
            return False

        log(f"找到 {len(cards)} 个课程", "info")

        if course_name:
            for card in cards:
                title = await card.query_selector(", ".join(SELECTORS["course"]["course_title"]))
                if title:
                    text = (await title.inner_text()).strip()
                    if course_name in text:
                        log(f"找到课程: {text}", "success")
                        await card.click()
                        await asyncio.sleep(3)
                        return True
            log(f"未找到课程: {course_name}", "error")
            return False
        else:
            # 默认进入第一个课程
            log("进入第一个课程", "info")
            await cards[0].click()
            await asyncio.sleep(3)
            return True

    async def navigate_to_unit(self, unit_name: str = None) -> bool:
        """导航到指定单元"""
        log("正在查找单元...", "step")
        await asyncio.sleep(2)

        units = await self.find_all(SELECTORS["learning"]["unit_list"])
        if not units:
            log("未找到单元列表", "warn")
            return False

        log(f"找到 {len(units)} 个单元", "info")

        if unit_name:
            for unit in units:
                title_el = await unit.query_selector(", ".join(SELECTORS["learning"]["unit_title"]))
                if title_el:
                    text = (await title_el.inner_text()).strip()
                    if unit_name in text:
                        log(f"找到单元: {text}", "success")
                        await unit.click()
                        await asyncio.sleep(2)
                        return True
            log(f"未找到单元: {unit_name}", "error")
            return False
        else:
            log("进入第一个单元", "info")
            await units[0].click()
            await asyncio.sleep(2)
            return True

    # ── 音频控制 ──

    async def get_audio_element(self) -> Optional[ElementHandle]:
        """获取音频/视频元素"""
        return await self.find_element(SELECTORS["audio"]["player"])

    async def play_audio(self) -> bool:
        """播放音频"""
        # 方法1: 点击播放按钮
        if await self.click_any(SELECTORS["audio"]["play_btn"]):
            log("已点击播放按钮", "info")
            return True

        # 方法2: 直接操作 audio/video 元素
        audio = await self.get_audio_element()
        if audio:
            await audio.evaluate("el => el.play()")
            log("已通过 JS 播放音频", "info")
            return True

        # 方法3: 全局播放
        await self.page.evaluate("""
            document.querySelectorAll('audio, video').forEach(el => el.play());
        """)
        log("已尝试全局播放所有媒体", "info")
        return True

    async def pause_audio(self) -> bool:
        """暂停音频"""
        if await self.click_any(SELECTORS["audio"]["pause_btn"]):
            return True
        await self.page.evaluate("""
            document.querySelectorAll('audio, video').forEach(el => el.pause());
        """)
        return True

    async def get_audio_duration(self) -> float:
        """获取音频总时长（秒）"""
        audio = await self.get_audio_element()
        if audio:
            duration = await audio.evaluate("el => el.duration")
            if duration and duration != float("inf"):
                return duration

        # 尝试从页面文本获取
        time_text = await self.get_text_any(SELECTORS["audio"]["total_time"])
        if time_text:
            return self._parse_time(time_text)

        return 0.0

    async def get_audio_current_time(self) -> float:
        """获取当前播放时间"""
        audio = await self.get_audio_element()
        if audio:
            return await audio.evaluate("el => el.currentTime") or 0.0
        time_text = await self.get_text_any(SELECTORS["audio"]["current_time"])
        if time_text:
            return self._parse_time(time_text)
        return 0.0

    async def wait_for_audio_end(self, extra_wait: float = 2.0):
        """等待音频播放完毕"""
        duration = await self.get_audio_duration()
        if duration > 0:
            log(f"音频时长: {duration:.1f}s，等待播放完成...", "info")
            # 轮询检查是否播放完毕
            while True:
                await asyncio.sleep(1)
                current = await self.get_audio_current_time()
                paused = await self.page.evaluate("""
                    (() => {
                        const a = document.querySelector('audio') || document.querySelector('video');
                        return a ? a.paused : true;
                    })()
                """)
                if paused and current >= duration - 0.5:
                    break
                if current >= duration:
                    break
            await asyncio.sleep(extra_wait)
            log("音频播放完成", "success")
        else:
            log("无法获取音频时长，等待默认 60s", "warn")
            await asyncio.sleep(60)

    async def get_audio_src(self) -> Optional[str]:
        """获取音频源 URL"""
        audio = await self.get_audio_element()
        if audio:
            src = await audio.evaluate("el => el.src || el.currentSrc")
            if src and not src.startswith("blob:"):
                return src

            # 检查 source 子元素
            sources = await audio.query_selector_all("source")
            for source in sources:
                src = await source.get_attribute("src")
                if src:
                    return src

        # 从网络请求中拦截
        return None

    # ── 题目操作 ──

    async def get_questions(self) -> list[dict]:
        """获取当前页面所有题目"""
        questions = []
        containers = await self.find_all(SELECTORS["question"]["container"])

        for i, container in enumerate(containers):
            q = {"index": i, "type": "unknown", "text": "", "options": []}

            # 题目文本
            text_el = await container.query_selector(", ".join(SELECTORS["question"]["question_text"]))
            if text_el:
                q["text"] = (await text_el.inner_text()).strip()

            # 选项
            option_els = await container.query_selector_all(", ".join(SELECTORS["question"]["options"]))
            for opt_el in option_els:
                opt_text = (await opt_el.inner_text()).strip()
                if opt_text:
                    q["options"].append({"element": opt_el, "text": opt_text})

            # 判断题型
            if q["options"]:
                q["type"] = "choice"
            else:
                blank_inputs = await container.query_selector_all(", ".join(SELECTORS["question"]["blank_input"]))
                if blank_inputs:
                    q["type"] = "blank"
                    q["inputs"] = blank_inputs

            questions.append(q)

        log(f"解析到 {len(questions)} 道题目", "info")
        return questions

    async def select_option(self, question_index: int, option_index: int) -> bool:
        """选择选择题选项"""
        questions = await self.get_questions()
        if question_index >= len(questions):
            log(f"题目索引越界: {question_index}", "error")
            return False

        q = questions[question_index]
        if option_index >= len(q["options"]):
            log(f"选项索引越界: {option_index}", "error")
            return False

        opt = q["options"][option_index]
        await opt["element"].click()
        log(f"第 {question_index + 1} 题: 选择了选项 {chr(65 + option_index)} - {opt['text']}", "info")
        return True

    async def fill_blank(self, question_index: int, answer: str) -> bool:
        """填写填空题"""
        questions = await self.get_questions()
        if question_index >= len(questions):
            return False

        q = questions[question_index]
        if q["type"] != "blank" or "inputs" not in q:
            log(f"第 {question_index + 1} 题不是填空题", "warn")
            return False

        # 如果只有一个空，直接填
        if len(q["inputs"]) == 1:
            await q["inputs"][0].fill(answer)
            log(f"第 {question_index + 1} 题: 填写答案 '{answer}'", "info")
            return True

        # 多个空的情况，按顺序填
        # 这里简化处理，实际可能需要更复杂的逻辑
        for i, inp in enumerate(q["inputs"]):
            if isinstance(answer, list) and i < len(answer):
                await inp.fill(answer[i])
            else:
                await inp.fill(str(answer))
        return True

    async def submit_answer(self) -> bool:
        """提交答案"""
        return await self.click_any(SELECTORS["question"]["submit_btn"])

    async def next_question(self) -> bool:
        """下一题"""
        return await self.click_any(SELECTORS["question"]["next_btn"])

    async def get_correct_answers(self) -> list[dict]:
        """获取正确答案（提交后）"""
        await asyncio.sleep(2)
        answers = []

        correct_els = await self.find_all(SELECTORS["result"]["correct_answer"])
        for el in correct_els:
            text = (await el.inner_text()).strip()
            if text:
                answers.append({"correct": text})

        # 获取解析
        explain_els = await self.find_all(SELECTORS["result"]["explanation"])
        for i, el in enumerate(explain_els):
            if i < len(answers):
                answers[i]["explanation"] = (await el.inner_text()).strip()

        return answers

    async def get_score(self) -> Optional[str]:
        """获取得分"""
        return await self.get_text_any(SELECTORS["result"]["score"])

    # ── 截图 & OCR ──

    async def screenshot(self, path: str = "screenshot.png") -> str:
        """截图"""
        await self.page.screenshot(path=path, full_page=False)
        log(f"截图已保存: {path}", "info")
        return path

    async def screenshot_element(self, selector: str, path: str = "element.png") -> Optional[str]:
        """截取指定元素"""
        el = await self.find_element(selector)
        if el:
            await el.screenshot(path=path)
            return path
        return None

    # ── 网络拦截 ──

    async def intercept_audio_requests(self) -> list[str]:
        """拦截并收集音频请求 URL"""
        audio_urls = []

        def handle_route(route):
            url = route.request.url
            if any(ext in url for ext in [".mp3", ".wav", ".ogg", ".m4a", ".aac", "audio"]):
                audio_urls.append(url)
                log(f"拦截到音频请求: {url[:100]}...", "info")
            route.continue_()

        await self.page.route("**/*", handle_route)
        return audio_urls

    # ── 工具方法 ──

    @staticmethod
    def _parse_time(time_str: str) -> float:
        """解析时间字符串 (e.g. '1:23' -> 83.0)"""
        parts = time_str.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                return float(parts[0])
        except (ValueError, IndexError):
            return 0.0

    async def get_page_html(self) -> str:
        """获取当前页面 HTML（用于调试）"""
        return await self.page.content()

    async def dump_page_info(self):
        """打印页面调试信息"""
        log("=== 页面调试信息 ===", "step")
        log(f"URL: {self.page.url}", "info")
        log(f"标题: {await self.page.title()}", "info")

        # 检查 audio 元素
        audios = await self.page.query_selector_all("audio, video")
        log(f"媒体元素数量: {len(audios)}", "info")

        # 检查 iframe
        iframes = await self.page.query_selector_all("iframe")
        log(f"iframe 数量: {len(iframes)}", "info")
        for i, frame in enumerate(iframes):
            src = await frame.get_attribute("src")
            log(f"  iframe[{i}]: {src}", "info")
