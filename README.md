# MugglesWand-IR

### 挥一下魔杖，重放你学过的红外指令。

MugglesWand-IR 在 [zoushui 的 Muggles' Wand](https://github.com/zou-shui/MugglesWand) 上加入 ESP32-C3 红外配件。魔杖识别手势，C3 学习遥控器信号；两者通过 ESP-NOW 通信。常用的红外信号可以在魔杖网页里重放，也可以分配给手势。

![Muggles' Wand 魔杖实拍](5.Others/Images/实拍图.jpeg)

> 魔杖外形、主板与原始手势项目来自 zoushui。本仓库保留原作者版权及 [MIT 许可](LICENSE)。红外配件是本版本新增的独立模块。

## 一眼看懂

| 魔杖 | 红外配件 | 网页控制台 |
| --- | --- | --- |
| ESP32-S3 + IMU 识别挥杖动作，提供照明、光绘与蓝牙遥控 | ESP32-C3 接收并保存红外信号，通过发射头重放 | 在同一个页面连接 C3、添加信号、手动重放并设置手势 |

**使用流程：** 连接配件 → 点击「添加信号」 → 对准接收头按一下遥控器 → 保存成功 → 点击重放，或把信号分配给一个手势。

![魔杖网页中的发送指令页面预览](5.Others/Images/魔杖控制界面预览.png)

*当前固件的页面预览；截图中的设备状态由浏览器模拟，实际连接状态以你的魔杖页面为准。*

## 红外学习与重放

在魔杖网页打开「配置 → 红外配件」，搜索并连接 C3。魔杖会记住这块配件。点「添加信号」后，C3 等待遥控器输入；学习成功便自动保存，列表只显示已学信号。可以为信号改名、重新学习、重放或删除。

![红外配件设置页面预览](5.Others/Images/红外配件界面预览.png)

*设置页面预览；截图中的连接地址和信号来自浏览器模拟。*

在「发送指令 → 红外配件」中可以直接选信号重放。在「配置 → 手势与灯效」中，可以把已学信号分配给魔杖手势。C3 当前最多保存 **32 个信号**；达到容量后删除旧信号即可继续添加。信号存于 C3，断电后保留。

## 除红外之外

- **照明与光绘：** 灯带提供照明和空中光绘，底部按键可返回手势识别。
- **蓝牙遥控：** 魔杖可进入隔空鼠标和音量控制模式。
- **Home Assistant：** 可单独配置并让手势触发场景。
- **自定义手势：** [训练工具](3.Software/4.GestureTrainer/README.md)支持采集动作、训练模型并导入魔杖。

![原项目的手势示意图](5.Others/Images/手势图解.png)

*图中展示原项目的内置手势；每个手势实际执行的功能以你保存的配置为准。*

## 开始使用

1. 给魔杖和 C3 红外配件供电。手机或电脑连接魔杖热点 `Muggles' Wand`，密码 `12345678`，打开 `http://192.168.4.1`。
2. 进入「配置 → 红外配件」，连接检测到的 C3。两块设备使用 ESP-NOW 信道 1；如果 Home Assistant 所用 Wi-Fi 在其他信道，页面会提示冲突。
3. 点击「添加信号」，在 60 秒内对准接收头短按遥控器。学习成功后，信号会出现在列表中。
4. 在「发送指令」中重放测试。确认受控设备响应后，再到「手势与灯效」中绑定。

### C3 接线

| 红外配件引脚 | ESP32-C3 Super Mini |
| --- | --- |
| 接收模块 OUT | GPIO7 |
| 发射模块 IN | GPIO20 |
| 两个模块 GND | 与 C3 共地 |
| 发射模块 VCC | 模块额定的 5V 电源 |

C3 的 GPIO 为 3.3V 电平。接收模块若由 5V 供电，接入 GPIO7 前须确认 OUT 电平安全；发射模块 IN 须支持 3.3V 控制。3W 发射头需由合适的 5V 电源供电，不能由 GPIO 直接供电。完整接线与固件说明见 [C3 开发文档](2.Firmware/IRLearner-C3/README.md)。

## 下载与开发

| 内容 | 位置 |
| --- | --- |
| 魔杖和 C3 应用固件 | [固件交付目录](2.Firmware/release-ir-espnow/) |
| 魔杖 ESP32-S3 源码 | [固件源码子仓库](https://github.com/WuYuStar/MugglesWand-IR-Firmware) |
| ESP32-C3 红外固件源码 | [IRLearner-C3](2.Firmware/IRLearner-C3/) |
| 手势训练工具 | [GestureTrainer](3.Software/4.GestureTrainer/) |
| 原版硬件与外壳资料 | [硬件目录](1.Hardware/) · [外壳目录](4.Model/) |

```bash
git clone --recurse-submodules https://github.com/WuYuStar/MugglesWand-IR.git
```

固件采用 PlatformIO 构建。魔杖已提供的升级页可以上传匹配的 ESP32-S3 应用固件；新 C3 首次烧录需要同时写入引导程序和分区表，不能只写应用镜像。更多说明见[固件交付文档](2.Firmware/release-ir-espnow/README.md)。

## 当前验证范围

两份固件已在 WSL 编译；魔杖页面的浏览器交互检查覆盖连接、学习、重放、手势配置及添加第 10 个信号。两块设备已完成固件写入校验，魔杖能正常启动。两块设备同时通电时的 ESP-NOW 连接、真实挥杖触发和不同遥控设备的兼容性仍需逐项实测。普通红外接收头无法测量原遥控器载波，因此空调长帧、长按重复和滚动码设备不保证重放成功。

## 致谢与许可

原项目由 [zoushui](https://github.com/zou-shui/MugglesWand) 开源。魔杖的硬件设计、外形、基础固件和原始手势资料均来自原项目；原版演示视频可在 [Bilibili](https://www.bilibili.com/video/BV14XaJ6nEzr/) 观看。原作者的 MIT 许可与版权声明见 [LICENSE](LICENSE) 和 [固件许可](2.Firmware/MugglesWand-src/LICENSE)。
