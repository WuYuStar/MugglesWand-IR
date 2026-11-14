# 魔杖与红外配件固件

本目录提供两块板的**应用镜像**。魔杖使用 ESP32-S3，红外配件使用 ESP32-C3 Super Mini；完整下载文件见 [v0.2.0-rc1 Release](https://github.com/WuYuStar/MugglesWand-IR/releases/tag/v0.2.0-rc1)。

| 文件 | 用途 |
| --- | --- |
| `wand-esp32s3-app.bin` | 在魔杖网页「设备 → 系统升级」上传 |
| `ir-esp32c3-app.bin` | 已按本项目分区表烧录的 C3 的应用更新 |
| `wand-esp32s3-factory.bin` | 新魔杖的完整首次烧录镜像，Release 下载 |
| `ir-esp32c3-factory.bin` | 新 C3 的完整首次烧录镜像，Release 下载 |
| `MugglesWand-Trainer-1.1.0-Windows-x64.zip` | Windows 手势训练独立版，Release 下载 |

首次烧录时，按芯片型号选择对应的 `factory.bin`，从 Flash 地址 `0x0` 写入。完整镜像会覆盖 NVS 设置区；已有设备日常更新请使用应用镜像。也可以按 [C3 固件说明](../IRLearner-C3/README.md)使用 PlatformIO 完整烧录。源码分别位于 [魔杖固件仓库](https://github.com/WuYuStar/MugglesWand-IR-Firmware)和 [C3 固件目录](../IRLearner-C3/)。

## 使用红外配件

给两块板供电，连接魔杖热点，打开 `http://192.168.4.1`：

1. 在「配置 → 红外配件」搜索并连接 C3。魔杖会记住已配对的设备。
2. 点击「添加信号」，在 60 秒内对准接收头短按遥控器。信号自动保存在 C3，断电后保留。
3. 在「已学信号」中重放、改名、重新学习或删除；也可到「发送指令 → 红外配件」直接重放，或在「配置 → 手势与灯效」绑定手势。

C3 最多保存 32 个信号。ESP-NOW 使用信道 1；若魔杖连接的家庭 Wi-Fi 在其他信道，页面会提示冲突。普通解调接收头不能测量原遥控器载波，部分空调长帧和滚动码设备可能无法重放。
