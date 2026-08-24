#!/usr/bin/env bash
# ==============================================================================
# MiPlay - Linux & Termux PRoot 一键安装与服务管理脚本 (2026 标准)
#
# 用法：
#   一键安装并启动服务:
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
GITHUB_API="https://api.github.com/repos/${REPO}/releases/latest"

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}        MiPlay 隔空妙播一键部署脚本             ${NC}"
echo -e "${BLUE}====================================================${NC}"

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

    # 删除二进制文件
    if [ -f "$BIN_PATH" ]; then
        echo -e "正在清理可执行文件: ${BIN_PATH}..."
        rm -f "$BIN_PATH"
    fi

    echo -e "${GREEN}🎉 [✓] MiPlay 已成功卸载！${NC}"
    echo -e "${YELLOW}注：用户配置文件 ~/.config/miplay 已完整保留，如需彻底清除请手动执行: rm -rf ~/.config/miplay${NC}"
    exit 0
fi

# ==================== 2. 依赖环境检测与自动安装 (FFmpeg) ====================
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${YELLOW}[!] 检测到系统未安装 FFmpeg 音频解码工具，正在自动安装...${NC}"
    if command -v apt-get >/dev/null 2>&1; then
        if [ "$(id -u)" -ne 0 ]; then
            sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
        else
            apt-get update -qq && apt-get install -y -qq ffmpeg
        fi
    elif command -v yum >/dev/null 2>&1; then
        if [ "$(id -u)" -ne 0 ]; then sudo yum install -y ffmpeg; else yum install -y ffmpeg; fi
    elif command -v apk >/dev/null 2>&1; then
        apk add --no-cache ffmpeg || true
    elif command -v pacman >/dev/null 2>&1; then
        if [ "$(id -u)" -ne 0 ]; then sudo pacman -Sy --noconfirm ffmpeg; else pacman -Sy --noconfirm ffmpeg; fi
    elif command -v pkg >/dev/null 2>&1; then # Termux
        pkg install -y ffmpeg || true
    elif command -v brew >/dev/null 2>&1; then # macOS Homebrew
        brew install ffmpeg || true
    else
        echo -e "${YELLOW}[!] 无法识别包管理器，请手动安装 FFmpeg: sudo apt install ffmpeg${NC}"
    fi
else
    echo -e "${GREEN}✓ 已检测到系统 FFmpeg 音频解码引擎就绪${NC}"
fi

# ==================== 3. 环境与架构检测 ====================
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)
        ASSET_NAME="miplay-linux-amd64"
        ;;
    aarch64|arm64)
        ASSET_NAME="miplay-linux-arm64"
        ;;
    *)
        echo -e "${RED}[x] 暂不支持当前 CPU 架构: ${ARCH}${NC}"
        exit 1
        ;;
esac

echo -e "检测到系统架构: ${GREEN}${ARCH}${NC} (匹配目标: ${ASSET_NAME})"

# ==================== 3. 下载二进制裸文件 ====================
echo -e "正在获取最新 Release 版本..."
DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${ASSET_NAME}"

echo -e "下载地址: ${BLUE}${DOWNLOAD_URL}${NC}"
TMP_FILE="/tmp/${ASSET_NAME}"

if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar -o "$TMP_FILE" "$DOWNLOAD_URL"
elif command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "$TMP_FILE" "$DOWNLOAD_URL"
else
    echo -e "${RED}[x] 未检测到 curl 或 wget 工具，请先安装。${NC}"
    exit 1
fi

chmod +x "$TMP_FILE"

# 移动至 /usr/local/bin (需要 root 权限)
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${YELLOW}移动二进制文件至 ${INSTALL_DIR} 需要 root 权限，请输入 sudo 密码：${NC}"
    sudo mv "$TMP_FILE" "$BIN_PATH"
else
    mv "$TMP_FILE" "$BIN_PATH"
fi

echo -e "${GREEN}✓ 二进制文件已成功安装到: ${BIN_PATH}${NC}"

# ==================== 4. 注册 Systemd 后台自启服务 ====================
if command -v systemctl >/dev/null 2>&1 && [ -d "/run/systemd/system" ]; then
    echo -e "正在注册 Systemd 开机自启服务..."
    if [ "$(id -u)" -ne 0 ]; then
        sudo "$BIN_PATH" service install
    else
        "$BIN_PATH" service install
    fi
else
    echo -e "${YELLOW}[!] 检测到当前环境非 Systemd (例如 Android Termux)，跳过服务安装。${NC}"
    echo -e "👉 建议使用守护模式后台运行: ${GREEN}${BIN_NAME} serve -d${NC}"
    echo -e "👉 停止后台运行: ${GREEN}${BIN_NAME} stop${NC}"
fi

echo -e "\n${GREEN}🎉 安装完成！请在浏览器访问 Web 控制台: http://<服务器IP>:8820${NC}\n"
