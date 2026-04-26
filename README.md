# MiPlay

MiPlay 是一个 AirPlay-only 的小米音箱无线桥接器。

它的职责很单一：

- 接收 AirPlay 1 / RAOP 音频
- 为每个已启用的小米音箱暴露一个独立的 AirPlay endpoint
- 把收到的音频流回推给对应的小米音箱播放
- 与外部 AirPlay 2 接收器共存，例如 `shairport-sync`

MiPlay 不包含 `shairport-sync`，也不尝试管理本机声卡输出。推荐分工如下：

- 有线音箱：外部 `shairport-sync` 负责 AirPlay 2
- 小米音箱：MiPlay 负责无线桥接

## 当前范围

- 保留 `RAOP` / `AirPlay 1` 接收能力
- 移除新主运行时中的 `DLNA / Plex / 语音控制`
- 使用新的配置模型 `xiaomi + targets[] + external`
- 提供 Web UI 管理小米账号、设备同步、AirPlay 名称和共存提示

## 启动

```bash
pip install .
miplay serve --conf-path conf
```

或：

```bash
python miplay.py
```

启动后访问：

```text
http://<你的主机IP>:8300
```

## 本地测试

macOS 上优先直接运行 Python 版本，先不要急着上 Docker：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
miplay serve --conf-path conf --host 你的局域网IP --web-port 8300
```

建议测试顺序：

1. 在 Web UI 里填入小米账号或 Cookie
2. 同步设备并只启用一个小米音箱 target
3. 用 iPhone / iPad / Mac 在同一局域网里搜索该 AirPlay 名称
4. 确认音频能桥接到目标小米音箱

如果你要在 macOS 上用 Docker Desktop 测试 `network_mode: host`，官方文档说明这只在 Docker Desktop 4.34+ 可用，而且需要手动开启 host networking。

## macOS AirPlay 冲突

MiPlay 当前的 Web UI 固定端口是 `8300`，AirPlay RTSP 和内部音频流端口都由程序动态分配，所以真正容易冲突的通常不是固定端口，而是“接收器名字”和“系统自带 AirPlay Receiver”。

macOS 官方文档说明可以在：

`System Settings > General > AirDrop & Handoff > AirPlay Receiver`

里开启 AirPlay Receiver。测试 MiPlay 时建议先把它关闭，原因是：

- iPhone / iPad 会同时发现你的 Mac 和 MiPlay
- 如果名称起得太像，容易选错目标
- 你后续再和外部 `shairport-sync` 共存时，也更容易判断是谁在广播

如果你不想关闭它，也可以共存，但要保证：

- MiPlay target 的 `airplay_name` 与系统 AirPlay 名称不同
- 外部 `shairport-sync` 的名称也不同
- `8300` 没有被别的服务占用

## 配置示例

见 [config-example.json](config-example.json)。

关键字段：

- `host`：MiPlay 广播给局域网的主机 IP
- `xiaomi.account / password / cookie`：小米账号信息
- `targets[]`：要桥接的小米音箱列表
- `targets[].airplay_name`：暴露给 iPhone / Mac 的 AirPlay 名称
- `external.wired_airplay_name`：外部有线 AirPlay 接收器名称，只用于名称冲突提示

## Docker Compose

只启动 MiPlay：

```bash
docker compose up -d
```

如需和外部 AirPlay 2 接收器一起部署：

```bash
docker compose -f compose.yml -f compose.external.yml up -d
```

`compose.external.yml` 只是示例，不会把 `shairport-sync` 作为 MiPlay 依赖。
