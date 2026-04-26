# MiPlay

MiPlay 是一个小米音箱的 AirPlay 无线桥接器，
> 本项目基于 [xiaomusic](https://github.com/hanxi/xiaomusic)、[airplay2-receiver](https://github.com/openairplay/airplay2-receiver)、[MiAir](https://github.com/KiriChen-Wind/MiAir) 二次开发，自用重构版本。

## ✨ 功能特色

- 小米音箱注册独立 AirPlay 设备
- 可与 `Shairport-Sync`搭配使用
    - 有线音箱：`Shairport-Sync` ➡️ 有线 AirPlay 2（多房间）
    - 小米音箱：MiPlay ➡️ 无线 AirPlay 1（多设备）

## 🚀 部署方式

### 1、Docker Compose
```
services:
  miplay:
    image: ghcr.io/juneix/miplay
    container_name: miplay
    network_mode: host
    restart: unless-stopped
    environment:
      WEB_PORT: 8300
      MIPLAY_HOST: ${MIPLAY_HOST:-}
    volumes:
      - ./conf:/app/conf
# 如需搭配 Shairport-Sync 使用，请取消注释
#  shairport-sync:
#    image: mikebrady/shairport-sync
#    container_name: airplay2
#    hostname: miplay 共存测试 
#    network_mode: host
#    restart: always
#    devices:
#      - /dev/snd:/dev/snd
#    cap_add:
#      - SYS_NICE
```


### 2、飞牛应用

飞牛商店的【AirPlay - 隔空播放】即将整合 MiPlay。


## ❤️ 支持项目

- 打赏鼓励：支持我开发更多有趣应用
- 互动群聊：加入 💬 [QQ 群](https://qm.qq.com/q/ZzOD5Qbhce) 可在线催更
- 更多内容：访问 ➡️ [谢週五の藏经阁](https://5nav.eu.org)

<div align="center">
  <table>
    <tr>
      <td align="center">
        <img src="./miplay/web/static/wechat.webp" width="128" /><br/>
        <sub>微信</sub>
      </td>
      <td align="center">
        <img src="./miplay/web/static/alipay.webp" width="128" /><br/>
        <sub>支付宝</sub>
      </td>
    </tr>
  </table>
</div>