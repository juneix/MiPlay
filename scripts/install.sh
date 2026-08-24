#!/usr/bin/env bash
# ==============================================================================
# MiPlay - Linux, macOS & Termux 极速一键部署脚本 (2026 标准)
#
# 用法：
#   一键安装并启动:
#     curl -fsSL https://raw.githubusercontent.com/juneix/MiPlay/main/scripts/install.sh | bash
#   一键卸载:
#     curl -fsSL https://raw.githubusercontent.com/juneix/MiPlay/main/scripts/install.sh | bash -s -- --uninstall
# ==============================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

INSTALL_DIR="/usr/local/bin"
BIN_NAME="miplay"
BIN_PATH="${INSTALL_DIR}/${BIN_NAME}"
SERVICE_FILE="/etc/systemd/system/miplay.service"

REPO="juneix/MiPlay"

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}        MiPlay 隔空妙播一键部署脚本             ${NC}"
echo -e "${BLUE}====================================================${NC}"

# ==================== 0. 网络环境探测与国内 CDN 加速配置 ====================
GH_PROXY=""
if ! curl -Is -m 1 https://github.com >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] 检测到国内网络环境，已自动激活高速 CDN 专线 (GHProxy & 阿里云镜像)${NC}"
    GH_PROXY="https://ghproxy.net/"
    export UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"
    export UV_PYTHON_INSTALL_MIRROR="https://ghproxy.net/https://github.com/astral-sh/python-build-standalone/releases/download"
else
    echo -e "${GREEN}✓ 国际互联网直连就绪${NC}"
fi

# ==================== 1. 卸载流程 ====================
if [ "$1" = "--uninstall" ] || [ "$1" = "uninstall" ]; then
    echo -e "${YELLOW}[!] 开始卸载 MiPlay...${NC}"
    
    # 停止并删除 systemd 服务
    if command -v systemctl >/dev/null 2>&1 && [ -f "$SERVICE_FILE" ]; then
        echo -e "正在停止并注销系统服务..."
        systemctl disable --now miplay >/dev/null 2>&1 || true
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi

    # 删除 uv tool
    if command -v uv >/dev/null 2>&1; then
        uv tool uninstall miplay-hub >/dev/null 2>&1 || uv tool uninstall miplay >/dev/null 2>&1 || true
    fi

    # 删除二进制文件
    if [ -f "$BIN_PATH" ]; then
        echo -e "正在清理可执行文件: ${BIN_PATH}..."
        rm -f "$BIN_PATH"
    fi
    rm -f "$HOME/.local/bin/miplay" "$HOME/.local/bin/miplay-desktop" "$HOME/Desktop/MiPlay.command" 2>/dev/null || true

    echo -e "${GREEN}🎉 [✓] MiPlay 已成功卸载！${NC}"
    echo -e "${YELLOW}注：用户配置文件 ~/.config/miplay 已完整保留，如需彻底清除请手动执行: rm -rf ~/.config/miplay${NC}"
    exit 0
fi

# ==================== 2. FFmpeg 可选交互检测与安装 ====================
if command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${GREEN}✓ 已检测到系统 FFmpeg 音频转码引擎就绪${NC}"
else
    echo -e "${YELLOW}[!] 检测到系统未安装 FFmpeg 音频转码引擎。${NC}"
    echo -e "    ${BLUE}说明：小米音箱硬件原生支持 MP3/M4A/FLAC/WAV/M3U8 直连播放；仅在需要非标准音频流实时转码时才依赖 FFmpeg。${NC}"
    
    INSTALL_FFMPEG="n"
    # 支持在 curl | bash 管道模式下通过 /dev/tty 读取用户交互选择
    if [ -t 0 ]; then
        read -r -p "👉 是否现在安装 FFmpeg？[y/N] (默认 N 跳过): " choice
        INSTALL_FFMPEG="${choice:-n}"
    elif [ -e /dev/tty ]; then
        read -r -p "👉 是否现在安装 FFmpeg？[y/N] (默认 N 跳过): " choice < /dev/tty 2>/dev/null || choice="n"
        INSTALL_FFMPEG="${choice:-n}"
    fi

    if [[ "$INSTALL_FFMPEG" =~ ^[Yy]$ ]]; then
        echo -e "正在通过系统包管理器安装 FFmpeg..."
        if command -v apk >/dev/null 2>&1; then # Alpine
            apk add --no-cache ffmpeg
        elif command -v apt-get >/dev/null 2>&1; then # Debian / Ubuntu
            if [ "$(id -u)" -ne 0 ]; then
                sudo apt-get update -qq && sudo apt-get install -y ffmpeg
            else
                apt-get update -qq && apt-get install -y ffmpeg
            fi
        elif command -v dnf >/dev/null 2>&1; then # Fedora / RHEL
            if [ "$(id -u)" -ne 0 ]; then sudo dnf install -y ffmpeg; else dnf install -y ffmpeg; fi
        elif command -v yum >/dev/null 2>&1; then # CentOS
            if [ "$(id -u)" -ne 0 ]; then sudo yum install -y ffmpeg; else yum install -y ffmpeg; fi
        elif command -v pacman >/dev/null 2>&1; then # Arch Linux
            if [ "$(id -u)" -ne 0 ]; then sudo pacman -Sy --noconfirm ffmpeg; else pacman -Sy --noconfirm ffmpeg; fi
        elif command -v pkg >/dev/null 2>&1; then # Termux
            pkg install -y ffmpeg
        elif command -v brew >/dev/null 2>&1; then # macOS Homebrew
            brew install ffmpeg
        else
            echo -e "${YELLOW}[!] 无法识别包管理器，请后续手动安装: sudo apt install ffmpeg${NC}"
        fi

        if command -v ffmpeg >/dev/null 2>&1; then
            echo -e "${GREEN}✓ FFmpeg 安装成功！${NC}"
        fi
    else
        echo -e "${YELLOW}[i] 已跳过 FFmpeg 安装（后续可随时手动安装）。${NC}"
    fi
fi

# ==================== 3. 极速包管理器 uv 检测与安装 ====================
if ! command -v uv >/dev/null 2>&1 && [ ! -f "$HOME/.local/bin/uv" ] && [ ! -f "$HOME/.cargo/bin/uv" ]; then
    echo -e "${YELLOW}[!] 正在安装极速运行工具 uv...${NC}"
    curl -LsSf "${GH_PROXY}https://astral.sh/uv/install.sh" | sh
fi

# 注入环境变量
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}[x] uv 安装失败，请检查网络或手动安装: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
    exit 1
fi

echo -e "${GREEN}✓ 极速运行引擎 uv 就绪: $(uv --version)${NC}"

# ==================== 4. 安装 miplay-hub ====================
echo -e "正在安装/更新 ${GREEN}miplay-hub${NC}..."

# 优先 PyPI 在线安装，失败时从 GitHub Releases wheel 兜底安装
WHEEL_FALLBACK="${GH_PROXY}https://github.com/${REPO}/releases/latest/download/miplay-1.0.1-py3-none-any.whl"
uv tool install --force miplay-hub 2>/dev/null || uv tool install --force "$WHEEL_FALLBACK"

USER_BIN="$HOME/.local/bin/miplay"
if [ ! -f "$USER_BIN" ]; then
    USER_BIN="$(command -v miplay 2>/dev/null || true)"
fi

# 软链接到全局 /usr/local/bin (若有权限)
if [ -n "$USER_BIN" ] && [ -f "$USER_BIN" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        cp -f "$USER_BIN" "$BIN_PATH" 2>/dev/null || true
    elif sudo -n true 2>/dev/null; then
        sudo cp -f "$USER_BIN" "$BIN_PATH" 2>/dev/null || true
    fi
fi

# ==================== 5. 注册后台自启服务或桌面快捷方式 ====================
OS="$(uname -s)"
if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1 && [ -d "/run/systemd/system" ]; then
    echo -e "正在注册 Systemd 开机自启服务..."
    if [ "$(id -u)" -ne 0 ]; then
        sudo "$USER_BIN" service install || true
    else
        "$USER_BIN" service install || true
    fi
elif [ "$OS" = "Darwin" ]; then
    DESKTOP_DIR="$HOME/Desktop"
    if [ -d "$DESKTOP_DIR" ]; then
        cat << 'EOF_MAC' > "$DESKTOP_DIR/MiPlay.command"
#!/usr/bin/env bash
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
miplay-desktop &
EOF_MAC
        chmod +x "$DESKTOP_DIR/MiPlay.command"
        echo -e "${GREEN}✓ 已在桌面生成启动快捷方式: ~/Desktop/MiPlay.command${NC}"
    fi
fi

echo -e "\n${GREEN}🎉 MiPlay 安装成功！${NC}"
echo -e "👉 桌面用户：双击桌面快捷方式或运行: ${GREEN}miplay-desktop${NC}"
echo -e "👉 命令行/NAS用户：运行服务: ${GREEN}miplay serve -d${NC}，停止服务: ${GREEN}miplay stop${NC}"
echo -e "👉 Web 控制台: ${BLUE}http://<本机IP>:8820${NC}\n"
