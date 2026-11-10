# MugglesWand-IR

一根能识别挥杖动作的魔杖，加上一块会学习遥控器信号的红外配件。先在网页里教它一个按键，再把这个信号交给手势：挥杖时，ESP32-C3 将学到的红外信号发出去。

![魔杖与红外配件的系统示意](5.Others/Images/ir-system.svg)

**魔杖负责识别与交互，红外配件负责收发。** 两块设备通过 ESP-NOW 通信，日常操作集中在魔杖自带的网页里。这个版本基于 [zoushui 的 Muggles' Wand](https://github.com/zou-shui/MugglesWand) 扩展；原项目的魔杖硬件、外壳和基础手势能力仍是整套体验的起点。

## 从一个遥控按键开始

1. 给魔杖与 ESP32-C3 红外配件供电。连接魔杖热点 `Muggles' Wand`，在浏览器打开 `http://192.168.4.1`。
2. 在「配置 → 红外配件」连接 C3，点击「添加信号」，对准接收头短按遥控器。
3. 学习成功后，信号会自动保存在 C3。先点「重放」确认受控设备有反应。
4. 在「配置 → 手势与灯效」给一个手势选择这个信号。之后也能在「发送指令」里直接重放。

![当前固件的红外配件设置页面](5.Others/Images/红外配件界面预览.png)

列表按需增长，只显示已学信号。可以改名、重新学习、重放或删除；C3 当前最多保存 **32 个**信号。魔杖记住已连接的 C3，信号存于 C3，断电后保留。

## 魔杖不止能发红外

原项目的 ESP32-S3 魔杖内置 IMU、灯带和蓝牙。这个版本在保留照明、空中光绘、隔空鼠标和音量控制的同时，把红外重放与 Home Assistant 场景放进同一套手势设置里。每个手势可分别选择功能和灯光效果。

![当前固件的发送指令页面](5.Others/Images/魔杖控制界面预览.png)

*这两张页面截图由当前固件页面生成，连接状态与信号名称使用模拟数据。页面样式和操作来自本仓库代码；实机连接结果以设备显示为准。*

想让魔杖识别自己的挥法，可以使用项目中的 [手势训练工具](3.Software/4.GestureTrainer/README.md) 采集动作、训练模型，再导入魔杖。红外配件、Home Assistant 和自定义手势各有独立配置入口。

## 设备与接线

| 部分 | 本版本中的作用 |
| --- | --- |
| ESP32-S3 魔杖 | 识别手势、显示网页、通过 ESP-NOW 控制红外配件 |
| ESP32-C3 Super Mini | 接收、保存并重放红外信号 |
| 红外接收模块 | `OUT → C3 GPIO7` |
| 红外发射模块 | `IN → C3 GPIO20`；VCC 使用模块额定的 5V，GND 与 C3 共地 |

C3 的 GPIO 为 3.3V 电平。接收模块使用 5V 供电时，接入 GPIO7 前应确认 OUT 的电压安全；发射模块 IN 也应支持 3.3V 控制。3W 发射模块需要合适的 5V 供电。接线和调试细节见 [红外配件固件文档](2.Firmware/IRLearner-C3/README.md)。

两块设备使用 ESP-NOW 信道 1。魔杖若同时接入 Home Assistant 所在的 Wi-Fi，而该网络不在信道 1，网页会提示信道冲突。

## 下载与项目结构

[下载 v0.1.0 固件](https://github.com/WuYuStar/MugglesWand-IR/releases/tag/v0.1.0) · [查看固件使用说明](2.Firmware/release-ir-espnow/README.md)

| 目录 | 内容 |
| --- | --- |
| [`2.Firmware/MugglesWand-src`](https://github.com/WuYuStar/MugglesWand-IR-Firmware) | 魔杖固件源码，作为 Git 子模块维护 |
| [`2.Firmware/IRLearner-C3`](2.Firmware/IRLearner-C3/) | C3 红外配件固件与分区表 |
| [`2.Firmware/release-ir-espnow`](2.Firmware/release-ir-espnow/) | 魔杖与 C3 的应用固件 |
| [`3.Software/4.GestureTrainer`](3.Software/4.GestureTrainer/) | 手势采集、训练与模型导入工具 |
| [`1.Hardware`](1.Hardware/) · [`4.Model`](4.Model/) | 原项目的硬件与外壳资料 |

```bash
git clone --recurse-submodules https://github.com/WuYuStar/MugglesWand-IR.git
```

魔杖可在现有升级页上传匹配的 ESP32-S3 应用固件。新 C3 首次烧录需要引导程序和分区表，请使用 C3 的 PlatformIO 工程完整上传。

## 验证与适用范围

魔杖与 C3 固件已在 WSL 编译，两个设备均完成写入校验，魔杖正常启动。网页交互检查覆盖连接、学习、命名、重放、删除、手势分配与添加第 10 个信号。两块设备同时通电时的 ESP-NOW 连接、真实挥杖触发，以及不同遥控设备的兼容性，仍需实机逐项验证。

红外接收头无法测出原遥控器的载波频率，重放默认使用 38 kHz。空调长帧、长按重复与滚动码设备不保证可用；最终以受控设备的实际响应为准。

## 来源与许可

魔杖硬件、外壳、原始固件与基础手势项目来自 [zoushui / MugglesWand](https://github.com/zou-shui/MugglesWand)。本仓库保留原作者的 [MIT 许可](LICENSE) 和 [固件许可](2.Firmware/MugglesWand-src/LICENSE)。本页的系统示意图按当前红外扩展结构绘制；页面截图来自本仓库的固件界面。
