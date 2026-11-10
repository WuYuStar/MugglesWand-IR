# POV 光绘图案工具

把 PNG、JPG、BMP、WebP 或 GIF 图片转换成魔杖空中光绘所需的 `CRGB` 数组。界面提供点阵预览和挥杖动画，并能把图案插入魔杖固件的 `src/APP/APP_POVPatterns.h`。

## 启动

在 WSL 中进入本目录，安装依赖并运行。图形窗口需要可用的 WSLg 显示环境。

```sh
python3 -m venv /home/codex/.venvs/wand-pov
/home/codex/.venvs/wand-pov/bin/python -m pip install -r requirements.txt
/home/codex/.venvs/wand-pov/bin/python main.py
```

## 制作图案

1. 选择图片或拖入窗口。工具按图片比例建议帧数，随后显示预览。
2. 设置图案名、LED 列数、帧数、扫描方向、镜像和去背景效果。图案名须是英文、数字或下划线组成的 C 标识符。
3. 在「挥舞动画」和「点阵预览」中检查效果。100% 预览速度对应固件约 10 ms/帧。
4. 复制 `CRGB` 代码、保存完整头文件，或选择「插入固件头文件」。插入前工具会备份原文件为 `.bak`；同名图案会整体替换。

当前魔杖图案使用 41 列数组。导出的代码须重新编译并更新魔杖固件后才会出现在网页图案列表；网页只能选择已经写入固件的图案。固件可对同一图案反向播放，不必为反向挥杖再导出一份。
