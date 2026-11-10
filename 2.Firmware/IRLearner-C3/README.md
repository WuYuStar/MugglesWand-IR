# ESP32-C3 Super Mini 红外学习和重发

独立的固件项目，与魔杖固件和训练应用分开。发射 IN 使用 **GPIO20**，接收 OUT 使用 **GPIO7**，串口使用原生 USB CDC（115200），不用 GPIO20/21 的 UART。

## 接线和模块兼容性

- ESP32-C3 的 GPIO 是 3.3V 电平。接收模块若由 5V 供电，必须确认 OUT 不超过芯片允许的输入电压；否则先断开 OUT，使用支持 3.3V 的接收模块供电方案或电平转换。
- 发射模块 VCC 接其额定 5V，GND 与 C3 共地，IN 接 GPIO20。必须确认 IN 支持 3.3V 控制，且不会向 GPIO20 反灌 5V。
- 确认模块接收的是载波脉冲，还是内置 38kHz 调制的包络信号。本固件输出带载波的脉冲，面向外部晶体管驱动型模块；若模块自带调制，需要另行适配。
- 3W 发射模块需要足够的 5V 电源，不能由 GPIO 或 3.3V 引脚供电。测试只发短帧。
- 未确认触发极性前，程序将 GPIO20 设为输入；外部驱动应有合适的默认关闭电阻。首次配置发射前，确认高/低有效，避免持续点亮。

## WSL 构建

```sh
/home/codex/.venvs/ir-firmware/bin/pio run -d /home/codex/project/muggleswand/2.Firmware/IRLearner-C3
```

USB 必须先共享并接入 WSL，出现 `/dev/ttyACM0` 等串口后再烧录。烧录会覆盖该板现有应用，应先用 esptool 读取整片 Flash 备份。

```sh
/home/codex/.venvs/ir-firmware/bin/pio run -d /home/codex/project/muggleswand/2.Firmware/IRLearner-C3 -t upload --upload-port /dev/ttyACM0
```

## 串口命令

每条命令以换行结束。控制脚本自动选择唯一的 Espressif USB 串口，也支持 `--port /dev/ttyACM0`。

```sh
/home/codex/.venvs/ir-firmware/bin/python /home/codex/project/muggleswand/2.Firmware/IRLearner-C3/tools/console.py --command status
```

| 命令 | 行为 |
| --- | --- |
| `status` | 显示 GPIO、电平、接收计数、是否有学习数据、存储状态 |
| `tx high` / `tx low` | 根据模块实际触发极性启用发射；每次启动均须配置 |
| `selftest` | 发送 38kHz NEC 测试帧，接收模块在 1.5 秒内回读完全匹配才报 PASS |
| `learn` | 等待 60 秒，保存第一份非重复、无溢出的有效脉冲数据 |
| `cancel` | 取消等待，不删除已学习数据 |
| `dump` | 导出学习数据 JSON，控制脚本自动保存到 `captures/` |
| `freq 38` | 设置重发载波频率，可用范围 20–60kHz |
| `send` | 发射一次已学习数据，接收器临时关闭以免回读污染数据 |
| `clear` | 删除已学习信号 |

学习时给脚本足够监听时间：`--command learn --seconds 65`。建议遥控器距接收头 10–30cm，短按一次，不长按。学习成功后输出 `LEARNED ... persistent=yes`，并保存到 NVS，重启后可用 `dump` 检查。自检时调整发射头与接收头的方向和距离，避免 3W 近距离导致饱和。PASS 证明这次光路收发成功；未收到不能直接断定模块损坏。

## 范围和限制

当前保存一个按键/一次捕获，缓冲区最多 4096 个时长项，帧间静默超过 50ms 即结束。溢出或重复帧不会覆盖已学习数据。原始时长转换跳过帧前空闲时间，将超过 65535µs 的时长拆成带零长反相间隔的序列。

接收头是解调器，无法测量原遥控器载波；默认 38kHz 只是初始设置。未知协议仍可保存原始时序，实际受控设备响应才是学习重发成功的最终依据。部分空调长帧、多包命令、长按重复和滚动码协议需要进一步适配；当前不会自动发送或开启无线网络。

参考：[IRremoteESP8266 官方库](https://github.com/crankyoldgit/IRremoteESP8266)、[Espressif ESP32-C3 数据手册](https://documentation.espressif.com/esp32-c3_datasheet_en.pdf)。
