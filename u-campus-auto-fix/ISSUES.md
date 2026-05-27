# U校园听力自动化 — 代码问题清单

## 项目说明

这是 U校园（unipus.cn）英语听力自动化答题脚本。用 Playwright 控制浏览器 + Whisper 语音识别 + 关键词匹配策略来答题。

当前环境：WSL Ubuntu 26.04，Playwright 内置 Chromium 无法下载（系统不支持），但系统安装了 `/snap/bin/chromium` 可用。

---

## P0 - 致命问题（不改不能跑）

### 问题1：chromium_path 未传入 → 程序无法启动

**文件**：`main.py`，第 69-73 行

```python
self.browser = BrowserEngine(
    headless=config.headless,
    slow_mo=config.slow_mo,
    user_data_dir=config.user_data_dir if not config.headless else None,
)
```

`BrowserEngine.__init__` 支持 `chromium_path` 参数（见 `browser_engine.py` 第 284 行），但 `UCampusAutomator.__init__` 从来不传。WSL 的 Playwright 内置 Chromium 无法下载，启动直接崩溃。

**修复方向**：
- `UCampusAutomator.__init__` 应该接受并传递 `chromium_path`
- 或在 `BrowserEngine.start()` 里自动检测系统 chromium（`/snap/bin/chromium`、`/usr/bin/chromium-browser` 等）

---

### 问题2：`_capture_via_cdp` 完全无效 —— 音频捕获失败

**文件**：`main.py`，第 407-438 行

```python
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
```

三个错误：
1. `createMediaElementSource(source)` 创建了音源，但**从未 connect 到 destination**，数据不会流到 MediaRecorder
2. `MediaRecorder` 构造后**从未调用 `.start()`**，不会录制
3. `MediaRecorder` 不接受 `AudioContext` 的 stream，它需要 `getUserMedia()` 的 MediaStream

**修复方向**：重新设计。正确方案是用 `page.route()` 拦截 .mp3/.m4a 网络请求，然后用 requests + cookies 下载。

---

### 问题3：系统录音捕获的是麦克风，不是浏览器声音

**文件**：`audio_engine.py`，第 97-103 行

```python
self._stream = sd.InputStream(
    samplerate=self.sample_rate,
    channels=self.channels,
    callback=callback,
    dtype="float32",
)
```

`sounddevice.InputStream` 打开的是**麦克风**（默认输入设备），不是系统音频输出。要捕获浏览器播放的声音需要 loopback 设备（Windows WASAPI loopback 或 Linux PulseAudio monitor），在 WSL 里基本不可行。

**修复方向**：这个捕获路径在 WSL 里应该被禁用，优先用网络下载方案（见问题4）。

---

## P1 - 严重问题（功能不可用）

### 问题4：音频 URL 下载缺少浏览器 cookies

**文件**：`audio_engine.py`，第 142-175 行 `AudioDownloader.download()`

```python
resp = requests.get(url, timeout=30, stream=True, headers={
    "User-Agent": "...",
    "Referer": "https://u.unipus.cn/",
})
```

U校园的音频 URL 几乎肯定需要登录 cookie/token。直接用 requests 裸请求会被 403。

**修复方向**：从 Playwright 的 browser context 提取 cookies，注入到 requests session 中。

---

### 问题5：`get_audio_src` 跳过 blob URL

**文件**：`browser_engine.py`，第 652-658 行

```python
src = await audio.evaluate("el => el.src || el.currentSrc")
if src and not src.startswith("blob:"):
    return src
```

U校园很可能用 `URL.createObjectURL()` 创建 blob URL 加载音频，代码直接跳过了。

**修复方向**：检测到 blob URL 时，用 `page.evaluate()` 读取 blob 数据转 base64 保存。

---

## P2 - 中等（不稳定/边缘情况）

### 问题6：大量硬编码 asyncio.sleep()，网络慢时不稳定

**文件**：`main.py` 和 `browser_engine.py`

| 位置 | 代码 |
|------|------|
| main.py:211 | `await asyncio.sleep(3)` 等待页面加载 |
| main.py:223 | `await asyncio.sleep(5)` 点击开始后等待 |
| main.py:253 | `await asyncio.sleep(3)` 提交后等待 |
| browser_engine.py:478 | `await asyncio.sleep(3)` 登录后检查URL |
| browser_engine.py:502 | `await asyncio.sleep(2)` 等待课程列表 |

特别是登录后 `await asyncio.sleep(3)` 然后检查 `"login" not in current_url`——网络慢时 redirect 还没完成就误判登录失败。

**修复方向**：全部换成 Playwright 原生等待：`page.wait_for_url()`、`page.wait_for_selector()`、`page.wait_for_load_state()`。

---

### 问题7：`wait_for_audio_end` 可能无限循环

**文件**：`browser_engine.py`，第 627-650 行

```python
while True:
    await asyncio.sleep(1)
    ...
    if paused and current >= duration - 0.5: break
    if current >= duration: break
```

如果音频元素的 `duration` 是 `NaN` 或 `Infinity`，`duration - 0.5` 也是 `NaN`，条件永远不成立 → 死循环。

**修复方向**：加超时保护（`max_wait = duration or 120`，`if elapsed > max_wait: break`）。

---

## P3 - 轻微问题

### 问题8：选项 inner_text 为空时被静默丢弃

**文件**：`browser_engine.py`，第 686-690 行

如果选项元素存在但 inner_text 为空（内容在子元素/图片里），选项被跳过 → 题目被识别为无选项 → 答题失败。

### 问题9：人名提取依赖首字母大写

**文件**：`audio_engine.py`，第 425-440 行 `extract_names()`

Whisper 转写可能全小写，人名提取就完全失效。而且排除了 "I" 这个词。

### 问题10：缺少 requirements.txt

项目没有依赖清单。

---

## 当前能正常工作的部分

- ✅ Playwright 浏览器控制（用 `/snap/bin/chromium` 即可启动）
- ✅ U校园登录页可正常访问
- ✅ SELECTORS 选择器配置完整（覆盖了多种 DOM 结构）
- ✅ 答题策略引擎逻辑正确（关键词匹配、排除法、数字匹配等）
- ✅ `TranscriptAnalyzer.find_best_answer` / `match_option` 正常工作
- ✅ 截图、页面调试功能正常

---

## 建议修复优先级

1. **先修 P0 问题 1+2**：让程序能启动 + 能捕获音频
2. **再修 P0 问题 3 + P1 问题 4+5**：打通音频获取链路（网络下载代替 CDP/录音）
3. **最后修 P2 问题 6+7**：让程序稳定可靠
4. P3 问题 8-10 可选修
