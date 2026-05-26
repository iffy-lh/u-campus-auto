# U校园听力自动化工具

## 功能

自动完成 U 校园（unipus.cn）英语听力课程的答题任务：

1. **自动登录** — 支持账号密码登录，保存登录态
2. **课程导航** — 自动进入指定课程和单元
3. **音频捕获** — 多种方式捕获听力音频（下载/系统录音/CDP）
4. **语音识别** — 使用 OpenAI Whisper 将音频转为文字
5. **智能答题** — 6种答题策略自动推理答案
6. **自动提交** — 自动选择答案并提交

## 答题策略

| 策略 | 适用题型 | 方法 |
|------|---------|------|
| keyword_match | 细节题/事实题 | 关键词重叠度匹配 |
| elimination | 含否定词的题目 | 排除被否定的选项 |
| number_match | 数字/时间/日期题 | 数字精确匹配 |
| name_match | 人名/地名题 | 专有名词匹配 |
| inference | 主旨/推断/态度题 | 关键词覆盖 + 语义分析 |
| conversation | 对话场景题 | 综合上下文推理 |

## 安装

### 1. 安装系统依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y ffmpeg portaudio19-dev python3-dev

# WSL (你的环境)
sudo apt install -y ffmpeg portaudio19-dev python3-dev libespeak-ng1
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器

```bash
playwright install chromium
```

### 4. 安装 Whisper 模型

首次运行时会自动下载。也可以手动下载：

```bash
python -c "import whisper; whisper.load_model('base')"
```

模型选择（精度 vs 速度）：
- `tiny` — 最快，精度最低 (~75MB)
- `base` — 推荐，平衡 (~140MB)
- `small` — 较好 (~460MB)
- `medium` — 高精度 (~1.5GB)
- `large` — 最高精度 (~3GB)，需要 GPU

## 使用

### 快速开始

```bash
# 第1步：生成配置模板
python main.py --init

# 第2步：编辑配置文件
vim config.json
# 填写你的学号、密码等信息

# 第3步：运行
python main.py
```

### 命令行参数

```bash
python main.py -u 学号 -p 密码                    # 直接指定账号
python main.py --model small                        # 使用更大的 Whisper 模型
python main.py --headless                          # 无头模式（不显示浏览器）
python main.py --debug                             # 调试模式
python main.py --course "新视野大学英语"             # 指定课程
python main.py --unit "Unit 1"                     # 指定单元
```

### 配置文件 (config.json)

```json
{
  "username": "你的学号",
  "password": "你的密码",
  "headless": false,
  "whisper_model": "base",
  "whisper_language": "en",
  "use_system_recorder": true,
  "max_listen_rounds": 3,
  "auto_submit": true,
  "debug": false
}
```

## 项目结构

```
u-campus-auto/
├── main.py              # 主程序入口
├── browser_engine.py    # 浏览器控制引擎
├── audio_engine.py      # 音频捕获与语音识别
├── answer_engine.py     # 答题策略引擎
├── requirements.txt     # Python 依赖
├── config.json          # 配置文件（自动生成）
├── audio_cache/         # 音频缓存目录
├── browser_data/        # 浏览器数据（登录态）
└── screenshots/         # 截图目录
```

## 工作流程

```
启动浏览器 → 登录 → 进入课程 → 进入单元
    ↓
扫描题目列表
    ↓
对每道题:
  1. 播放听力音频
  2. 捕获音频（下载/录音）
  3. Whisper 转文字
  4. 策略引擎推理答案
  5. 在页面上选择答案
    ↓
提交答案 → 获取成绩 → 打印报告
```

## 注意事项

1. **首次运行** 建议用 `--debug` 模式，会输出详细日志和截图
2. **验证码** 如果遇到验证码，程序会截图并等待手动输入
3. **音频捕获** 优先尝试下载音频 URL，失败则使用系统录音
4. **Whisper 模型** 推荐先用 `base`，如果识别效果不好换 `small` 或 `medium`
5. **登录态** 保存在 `browser_data/` 目录，下次运行自动复用
6. **U校园改版** 如果 U 校园页面结构变化，可能需要更新 `browser_engine.py` 中的选择器

## 常见问题

**Q: Whisper 识别不准确？**
A: 换更大的模型（`--model small`），或增加 `max_listen_rounds` 多听几遍

**Q: 录音没有声音？**
A: 检查系统音频设置，WSL 环境下可能需要配置 PulseAudio 或走 Windows 音频

**Q: 页面元素找不到？**
A: 运行 `--debug` 模式，查看 `page_debug.html` 分析页面结构

**Q: 登录失败？**
A: 检查账号密码，注意是否需要验证码
