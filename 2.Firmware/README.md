# 魔杖固件

这里包含魔杖本体、红外配件和原项目执行器的固件资料。[6.4 魔杖固件稳定版](releases/6.4/)提供 OTA 和首次烧录镜像，可查看适配范围与验收状态。修改源码时使用 PlatformIO。

| 目录 | 用途 |
| --- | --- |
| [MugglesWand-src](MugglesWand-src/) | ESP32-S3 魔杖固件源码，作为 Git 子模块维护 |
| [IRLearner-C3](IRLearner-C3/) | ESP32-C3 红外学习与重放配件源码 |
| [releases/6.4](releases/6.4/) | 6.4 稳定版，包含 42607-P 自动休眠与动作唤醒 |
| [releases/6.4.1](releases/6.4.1/) | 6.4 稳定版的实机验收构建 |
| [release-ir-espnow](release-ir-espnow/) | C3 应用镜像和魔杖 6.3-rc1 基线 |
| `EspNow_Slave-src` | 原项目的 ESP-NOW 舵机开关灯执行器源码 |
| `MugglesWand-firmware` | 原项目留下的预编译固件 |

克隆源码时使用 `git clone --recurse-submodules`。魔杖网页中的「设备 → 系统升级」可上传匹配的魔杖应用固件；新 C3 首次烧录需要完整写入引导程序、分区表和应用，按 [C3 固件说明](IRLearner-C3/README.md)操作。
