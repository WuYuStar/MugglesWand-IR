# 旧版 TensorFlow 手势训练脚本

`train.py` 读取同目录下的数字标签 CSV，训练 1D-CNN 并导出全整数 INT8 模型 `gesture_model.tflite`。这个脚本用于**需要自行修改和重编译固件**的旧流程。新固件的独立模型上传使用 `.wandmodel`，请优先用[魔杖手势训练](../4.GestureTrainer/README.md)。

## 准备数据

在本目录放入 `0.csv`、`1.csv` 等文件，编号必须从 0 连续递增，不能混入其他 CSV。每行格式为 `label,x0,y0,...,x99,y99`，共 1 个标签和 200 个数值；行内标签要与文件名一致。可用[原始数据采集脚本](../1.OriginalData/DataCollector/README.md)制作数据。

## 运行

在 WSL 中进入本目录，创建虚拟环境并安装固定版本的依赖：

```sh
python3 -m venv /home/codex/.venvs/wand-legacy-tf
/home/codex/.venvs/wand-legacy-tf/bin/python -m pip install -r requirements.txt
/home/codex/.venvs/wand-legacy-tf/bin/python train.py
```

脚本输出测试准确率、归一化参数 `mean` 和 `std`，并生成 `gesture_model.tflite`。旧固件集成时，还需把模型转成 C 数组，更新模型头文件、归一化参数和类别数，再编译固件。这些输出不能直接上传到新版魔杖的「模型更新」入口。
