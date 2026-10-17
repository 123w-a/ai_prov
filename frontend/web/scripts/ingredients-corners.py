# -*- coding: utf-8 -*-
"""把「背景一致」从口头声称变成数字：逐张读四角与中心的真实 RGB。

用法：
    D:\\develop\\ai_prov\\.venv\\Scripts\\python.exe scripts\\ingredients-corners.py

为什么是四角（而不是"看一眼挺一致"）：
  这批图是**棚拍浅暖白背景**，背景色是否齐一，只体现在四周那圈像素上。
  四角各有主体遮不到的位置，且 (2,2) 与 (253,253) 分处对角，
  能同时覆盖"渐变方向不一致"和"某张阴影糊到角上"两类问题——
  前者看范围，后者看**单张四角内部是否自洽**（同一张里某角明显偏暗 = 有东西压上去）。

输出的三种判据：
  ① 全 40 张四角 R/G/B 各自的 min–max；
  ② 单张四角极差（自己四个角之间的最大差）——大者说明该角被主体/阴影碰到；
  ③ 与**已验收 8 张的实测包络**的比对（那 8 张是风格基准，不是某个理想值）。
"""

import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.normpath(os.path.join(HERE, "..", "public", "assets", "food", "ingredients"))

# 上一轮已验收的 8 张：它们是风格的**定义**，所以基准包络从它们实测得出，
# 而不是从任务书里那句近似值反推。
REF8 = ["鸡蛋", "西兰花", "面条", "大蒜", "番茄", "土豆", "青椒", "豆腐"]

# 任务书给的窄带（用于如实报告"按这个口径有几张越界"）。
BAND = {"R": (240, 252), "G": (236, 248), "B": (228, 242)}

CORNERS = [(2, 2), (253, 2), (2, 253), (253, 253)]


def corners(path: str):
    im = Image.open(path).convert("RGB")
    return im.size, [im.getpixel(p) for p in CORNERS], im.getpixel((128, 128))


def main() -> int:
    names = sorted(
        os.path.splitext(f)[0] for f in os.listdir(IMAGE_DIR) if f.lower().endswith(".png")
    )
    rows = []
    print(f"{'文件':<10}{'c(2,2)':<14}{'c(253,2)':<14}{'c(2,253)':<14}{'c(253,253)':<14}{'中心':<14}角落极差")
    for name in names:
        size, cs, ctr = corners(os.path.join(IMAGE_DIR, name + ".png"))
        assert size == (256, 256), f"{name} 不是 256x256: {size}"
        spread = max(
            max(c[i] for c in cs) - min(c[i] for c in cs) for i in range(3)
        )
        rows.append((name, cs, spread))
        cells = "".join(f"{c[0]:>3d},{c[1]:>3d},{c[2]:>3d}  " for c in cs)
        print(f"{name:<10}{cells}{ctr[0]:>3d},{ctr[1]:>3d},{ctr[2]:>3d}   {spread:>3d}")

    ref = [r for r in rows if r[0] in REF8]
    new = [r for r in rows if r[0] not in REF8]

    def envelope(group):
        flat = [c for _, cs, _ in group for c in cs]
        return {i: (min(c[i] for c in flat), max(c[i] for c in flat)) for i in range(3)}

    print()
    print(f"共 {len(rows)} 张（参考 8 + 本批 {len(new)}）")
    for label, group in (("参考 8 张", ref), ("本批 32 张", new), ("全 40 张", rows)):
        env = envelope(group)
        print(
            f"{label} 四角包络：R {env[0][0]}–{env[0][1]}  "
            f"G {env[1][0]}–{env[1][1]}  B {env[2][0]}–{env[2][1]}"
        )

    refenv = envelope(ref)
    print()
    print("① 按任务书窄带 R240–252 / G236–248 / B228–242 判定：")
    band_bad = []
    for label, group in (("参考 8 张", ref), ("本批 32 张", new)):
        bad = []
        for name, cs, _ in group:
            for c in cs:
                for i, k in enumerate("RGB"):
                    lo, hi = BAND[k]
                    if not (lo <= c[i] <= hi):
                        bad.append((name, k, c[i]))
                        break
                else:
                    continue
                break
        band_bad.append((label, bad))
        print(f"  {label}：{len(bad)} 张越界 " + ("、".join(f"{n}({k}={v})" for n, k, v in bad) or ""))

    print()
    print(
        f"② 按**参考 8 张实测包络** R{refenv[0][0]}–{refenv[0][1]} / G{refenv[1][0]}–{refenv[1][1]}"
        f" / B{refenv[2][0]}–{refenv[2][1]}（允许 ±2 容差）判定："
    )
    tol = 2
    outside = []
    for name, cs, _ in rows:
        worst = []
        for c in cs:
            for i in range(3):
                lo = refenv[i][0] - tol
                hi = refenv[i][1] + tol
                if not (lo <= c[i] <= hi):
                    worst.append(f"{'RGB'[i]}={c[i]} 越 {'R' if i==0 else 'G' if i==1 else 'B'} 包络[{refenv[i][0]},{refenv[i][1]}]")
        if worst:
            outside.append((name, worst[0]))
    if outside:
        for n, w in outside:
            print(f"  {n}：{w}")
    else:
        print("  无：全部 40 张四角都落在参考包络 ±2 内")

    print()
    print("③ 单张四角极差 > 8 的（角落可能被主体/阴影碰到）：")
    hot = [(n, s) for n, _, s in rows if s > 8]
    print("  " + ("、".join(f"{n}(极差{s})" for n, s in hot) if hot else "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
