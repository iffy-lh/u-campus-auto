#!/bin/bash
# U校园听力自动化 - 一键安装脚本
# 用法: bash setup.sh

set -e

echo "========================================="
echo "  U校园听力自动化 - 安装脚本"
echo "========================================="
echo ""

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 1. 检查 Python ──
info "检查 Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
    info "Python 版本: $($PYTHON --version)"
else
    error "未找到 Python3，请先安装: sudo apt install python3"
    exit 1
fi

# ── 2. 检查 pip ──
info "检查 pip..."
if command -v pip3 &>/dev/null; then
    PIP=pip3
elif $PYTHON -m pip --version &>/dev/null; then
    PIP="$PYTHON -m pip"
else
    warn "pip 未安装，尝试安装..."
    sudo apt install -y python3-pip
    PIP=pip3
fi
info "pip: $($PIP --version)"

# ── 3. 安装系统依赖 ──
info "安装系统依赖..."
sudo apt update -qq
sudo apt install -y \
    ffmpeg \
    portaudio19-dev \
    python3-dev \
    libespeak-ng1 \
    chromium-browser \
    2>/dev/null || warn "部分系统包安装失败，可能不影响使用"

# ── 4. 安装 Python 依赖 ──
info "安装 Python 依赖..."

# 先装小的包
$PIP install --break-system-packages \
    requests \
    rich \
    pyyaml \
    Pillow \
    numpy \
    scipy \
    2>/dev/null || warn "部分小包安装失败"

# 装 Playwright（如果还没装）
$PIP install --break-system-packages playwright 2>/dev/null || true

# 装音频处理
$PIP install --break-system-packages \
    sounddevice \
    soundfile \
    pydub \
    2>/dev/null || warn "音频处理库安装失败，录音功能可能不可用"

# 装 Whisper（最大的包，最后装）
info "安装 OpenAI Whisper（较大，可能需要几分钟）..."
$PIP install --break-system-packages --timeout 600 openai-whisper 2>/dev/null || {
    warn "Whisper 安装失败（网络问题？）"
    warn "可以稍后手动安装: pip3 install openai-whisper"
    warn "或使用国内镜像: pip3 install -i https://mirrors.aliyun.com/pypi/simple/ openai-whisper"
}

# ── 5. 安装 Playwright 浏览器 ──
info "安装 Playwright Chromium..."
$PYTHON -m playwright install chromium 2>/dev/null || {
    warn "Playwright 浏览器安装失败"
    warn "可以尝试: sudo apt install chromium-browser"
    warn "或手动下载: https://playwright.azureedge.net/builds/chromium/"
}

# ── 6. 创建必要目录 ──
info "创建项目目录..."
mkdir -p audio_cache browser_data screenshots

# ── 7. 生成配置模板 ──
if [ ! -f config.json ]; then
    info "生成配置模板..."
    $PYTHON main.py --init
else
    info "配置文件已存在，跳过"
fi

# ── 8. 验证 ──
echo ""
info "验证安装..."

$PYTHON -c "
import sys
mods = [
    ('playwright', '浏览器自动化'),
    ('whisper', '语音识别'),
    ('sounddevice', '音频录制'),
    ('requests', '网络请求'),
    ('rich', '终端美化'),
]
for name, desc in mods:
    try:
        __import__(name)
        print(f'  ✓ {name} ({desc})')
    except ImportError:
        print(f'  ✗ {name} ({desc}) - 未安装')
" || true

echo ""
info "安装完成！"
echo ""
echo "使用方法:"
echo "  1. 编辑配置: vim config.json"
echo "  2. 运行:     python3 main.py"
echo "  3. 调试模式: python3 main.py --debug"
echo ""
