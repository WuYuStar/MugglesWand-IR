# Software

本目录存放在PC上运行的Python脚本和tf模型

## 1.OriginalData

内有DataCollector脚本源码以及打包好的可执行文件`DataCollector.exe`，后者可直接运行（无需Python环境）。

另外附有固件中模型的原始数据（csv）。

## 2.TensorFLow

内有利用原始数据对模型进行训练的程序`train.py`。需要自行配置python环境后运行。详细步骤请参阅内部的`README.md`。

## 3.POVTool

该工具是一个带有UI的可视化上位机，用于将任意图片转换成数组，供光绘模块使用。需要自行配置python环境后运行，详细步骤请参阅内部的`README.md`。









## 4.GestureTrainer

面向新手的电脑端手势训练工具：支持8～16个手势，每类至少120条，集成采集、检查数据、训练、独立测试与模型上传。电脑只管理手势与模型；功能、灯效和对应配置由魔杖固件独立负责。日常训练无需编译或升级整个固件。启动方式见 [工具说明](./4.GestureTrainer/README.md)。
