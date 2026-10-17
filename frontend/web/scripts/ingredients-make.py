# -*- coding: utf-8 -*-
"""生图（1024×1024）压成 256×256 的食材图，落进 public/assets/food/ingredients/。

用法：
    D:\\develop\\ai_prov\\.venv\\Scripts\\python.exe scripts\\ingredients-make.py <原始图目录>

两条纪律（2026-10-12 第二轮补图时定的）：
  1. **同目录临时文件 + os.replace**：直接 Image.save(目标) 会先截断再写，
     中途失败就留下半张图；而这张图正被 dev server 和浏览器读。
     os.replace 在同一卷上是原子的，所以要么是旧图，要么是新图，不存在半写态。
  2. **只用 LANCZOS 缩放，不做任何调色/裁切/背景归一化**。
     风格基准是已验收的那 8 张，任何颜色处理都会引入一个新变量，
     让"这一批和上一批一致"从事实退化成主张。角落色差用
     ingredients-corners.py 量出来如实报告，而不是靠调色把它"修"进区间。

只处理 <图片名>.png → <图片名>.png，文件名即规范中文名（与注册表逐字一致）。
"""

import os
import sys

from PIL import Image

SIZE = 256
# 上一轮已验收的 8 张 —— 不会被本脚本覆盖（本批只补后 32 样）。
DONE = {"鸡蛋", "西兰花", "面条", "大蒜", "番茄", "土豆", "青椒", "豆腐"}

TARGET = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "public", "assets", "food", "ingredients"
)


def convert(raw_path: str, out_path: str) -> tuple[bytes, ...]:
    im = Image.open(raw_path).convert("RGB")
    if im.size != (SIZE, SIZE):
        im = im.resize((SIZE, SIZE), Image.LANCZOS)
    tmp = out_path + ".tmp"
    im.save(tmp, format="PNG", optimize=True)
    os.replace(tmp, out_path)  # 原子：不留半写文件
    return (im.size,)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: ingredients-make.py <原始图目录>")
        return 2
    raw_dir = sys.argv[1]
    target = os.path.abspath(TARGET)

    names = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(raw_dir)
        if f.lower().endswith(".png") and not f.startswith("_")
    )
    made = 0
    for name in names:
        if name in DONE:
            print(f"跳过（上一轮已验收，不重做）：{name}")
            continue
        out = os.path.join(target, name + ".png")
        convert(os.path.join(raw_dir, name + ".png"), out)
        print(f"{name}.png  {os.path.getsize(out):>7d} 字节  256x256")
        made += 1
    print(f"共 {made} 张落盘 → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
