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

# ==================== 0. 安装源选择 ====================
echo -e "${YELLOW}请选择安装环境与镜像源:${NC}"
echo -e "  1) 国内源 (GHProxy 加速 + 阿里云镜像源)"
echo -e "  2) 国际源 (GitHub 直连 + PyPI 官方源)"
echo -n "👉 请输入选项 [1/2] (回车默认 1): "
read -r SOURCE_OPT < /dev/tty 2>/dev/null || read -r SOURCE_OPT || true
SOURCE_OPT="${SOURCE_OPT:-1}"
echo ""

if [ "$SOURCE_OPT" = "2" ]; then
    echo -e "${GREEN}✓ 已选择：国际官方源${NC}"
    GH_PROXY=""
else
    echo -e "${GREEN}✓ 已选择：国内镜像源${NC}"
    export UV_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"
    export UV_PYTHON_INSTALL_MIRROR="https://ghproxy.net/https://github.com/astral-sh/python-build-standalone/releases/download"
    GH_PROXY="https://ghproxy.net/"
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

# ==================== 2. FFmpeg 检测与选择 ====================
if command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${GREEN}✓ 已检测到系统已安装 FFmpeg${NC}"
else
    echo -e "${YELLOW}[!] 检测到系统未安装 FFmpeg 音频工具。${NC}"
    echo -n "👉 是否安装 FFmpeg？[y/N] (回车默认 N): "
    read -r INSTALL_FFMPEG < /dev/tty 2>/dev/null || read -r INSTALL_FFMPEG || true
    INSTALL_FFMPEG="${INSTALL_FFMPEG:-n}"
    echo ""

    if [ "$INSTALL_FFMPEG" = "y" ] || [ "$INSTALL_FFMPEG" = "Y" ]; then
        echo -e "正在安装 FFmpeg..."
        if command -v apk >/dev/null 2>&1; then # Alpine
            apk add --no-cache ffmpeg
        elif command -v apt-get >/dev/null 2>&1; then # Debian / Ubuntu
            if [ "$(id -u)" -ne 0 ]; then
                sudo apt-get update && sudo apt-get install -y ffmpeg
            else
                apt-get update && apt-get install -y ffmpeg
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
        echo -e "${YELLOW}[i] 已跳过 FFmpeg 安装。${NC}"
    fi
fi

# ==================== 3. 极速包管理器 uv 检测与安装 ====================
if ! command -v uv >/dev/null 2>&1 && [ ! -f "$HOME/.local/bin/uv" ] && [ ! -f "$HOME/.cargo/bin/uv" ]; then
    echo -e "正在安装极速包管理器 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null || curl -LsSf "${GH_PROXY}https://raw.githubusercontent.com/astral-sh/uv/main/assets/install/install.sh" | sh 2>/dev/null || true
fi

# 注入环境变量
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
    echo -e "${RED}[x] uv 安装失败，请检查网络或手动安装: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
    exit 1
fi

echo -e "${GREEN}✓ uv 就绪: $(uv --version)${NC}"

# ==================== 4. 安装 miplay-hub ====================
echo -e "正在安装 ${GREEN}miplay-hub${NC}..."

# 直接使用 uv tool install 安装
# 若国内镜像源尚未同步完成，自动回退官方源
uv tool install --force miplay-hub || uv tool install --force --default-index https://pypi.org/simple miplay-hub

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
    echo -n "👉 是否开机自启动 MiPlay？[Y/n] (回车默认 Y): "
    read -r AUTO_START < /dev/tty 2>/dev/null || read -r AUTO_START || true
    AUTO_START="${AUTO_START:-y}"
    echo ""

    if [ "$AUTO_START" = "y" ] || [ "$AUTO_START" = "Y" ]; then
        echo -e "正在注册 Systemd 开机自启服务..."
        if [ "$(id -u)" -ne 0 ]; then
            sudo "$USER_BIN" service install || true
        else
            "$USER_BIN" service install || true
        fi
    else
        echo -e "${YELLOW}[i] 已跳过开机自启（后续可随时手动运行: ${GREEN}miplay service install${YELLOW} 开启）。${NC}"
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
