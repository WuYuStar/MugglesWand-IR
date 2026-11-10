# 电脑端工具

本目录包含手势训练、原始数据采集和空中光绘图案制作程序。**首次使用新模型，优先选择[魔杖手势训练](4.GestureTrainer/README.md)**：它把录制、检查、训练、测试和上传放在同一界面，不需要重新编译魔杖固件。

| 工具 | 适用场景 |
| --- | --- |
| [魔杖手势训练](4.GestureTrainer/README.md) | 日常录制手势、训练 INT8 模型并上传到魔杖 |
| [原始数据采集](1.OriginalData/DataCollector/README.md) | 通过魔杖 TCP 数据流采集带数字标签的 CSV，供旧流程或研究使用 |
| [TensorFlow 训练脚本](2.TensorFlow/README.md) | 使用数字标签 CSV 生成旧式 TFLite 模型，需自行修改固件 |
| [POV 光绘工具](3.POVTool/README.md) | 把图片变成固件可用的灯带图案代码 |

这些工具运行在电脑上。魔杖的功能与灯效仍在魔杖网页「配置 → 手势与灯效」中设置。
