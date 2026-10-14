# 小膳管家 · 设计参考图生成提示词（投喂 kele_image_gen / gpt-image-2）

## 一、这张图画什么（决策）

**画：两屏并排 + 底部一条 token 带。一张图，不拆组件。**

- 左屏 = 生成中（请求确认卡），右屏 = 生成后（菜谱卡）。
- 两块都按手机真实比例 9:19.5 画，正视、纯平、无手机外壳、无投影、无透视。
- 画布底部加一条 120px 高的扁平色卡/字阶带，标出 5 个色值和 5 档字号。

**不画：** 组件拆解图（会退化成 UI kit 海报，丢掉真实的排版节奏和留白比例）、单屏特写（看不到两屏是同一套系统）、任何 3D 手机模型/手持手机/暖光场景图（那是概念插画，没法照着写 CSS）。

**中文文字策略（关键）：** 生图模型渲染小号中文会糊。提示词里只允许出现固定的短字符串清单（菜名、阶段词、人名、数值），其余正文一律要求模型画成**浅灰占位条**，不要编造中文。这样版式块可见、又不会满屏乱码。

## 二、主提示词（直接投喂）

```
Flat vector UI design spec sheet. Two mobile app screens side by side, each at exact
real phone proportion 9:19.5, straight-on view. No device mockup, no phone bezel, no
notch rendering, no hands, no perspective, no 3D, no drop shadow, no reflection. Pure
flat graphic design, like a Figma frame export on a light canvas.

CANVAS: solid flat background #F4F1EB. No gradient, no noise, no grain, no texture, no
vignette, no glassmorphism, no frosted glass, no blur, no glow, no neon.

STYLE: mature, restrained, slightly technical. Exactly ONE accent color — terracotta
#a96f4e — used only as solid fills and thin rules. Everything else is off-white, ink
gray and hairline strokes. No food photography, no food illustration, no cartoon, no
mascot, no emoji, no multicolor icons, no warm-light filter.

LEFT SCREEN — "request confirmation card", waiting state. It must read like a receipt
that is already filled in, static and settled:
- Top: one Chinese line 「正在为小美整理今晚的选择」, 18px, semibold, ink #2E2A26, left aligned.
- Below: one large calm card, white #FBF9F6 fill, 1px hairline border #E6E0D8, 14px
  radius, generous padding. Inside, a two-column fact list, no grid lines, no header row:
  row 「冰箱现有」 → 「鸡蛋」「西兰花」「面条」「大蒜」
  row 「家庭档案」 → 「小美 · 孕妇」「我 · 增肌 · 176/63」
- A single 2px terracotta rule, 32px wide, used as a section marker.
- Lower third: one outline-only button, 1px terracotta border, transparent fill,
  terracotta text 「取消本次请求」, full width, 44px tall, 12px radius.
- Under it: one thin row 「处理详情」 with a small chevron.
- Very bottom, small and de-emphasized: a 6px solid dot in muted green #7A8B6F followed
  by 12px gray text 「连接正常 · 结果尚未返回」; above it a single 12px gray line
  「最近收到：正在查证」.
- ABSOLUTELY NO progress bar, no percentage, no countdown, no remaining-time text, no
  spinner, no loading ring, no circular progress, no pulsing dots, no heartbeat line, no
  waveform, no ECG, no equalizer, no animation indicators.

RIGHT SCREEN — "recipe card", result state:
- Top: dish name 「蒜香西兰花鸡蛋面」, 24px bold ink #2E2A26, then two lines of light-gray
  intro as soft placeholder bars.
- Serving block: two rows only, NO table borders and NO full grid — just 1px hairline row
  separators. Row 「小美」 and row 「我」, each with a terracotta-filled horizontal bar as
  the portion indicator plus a small gray weight label.
- Ratings: small terracotta filled stars, 5 per row, 12px, inline with 13px gray labels
  「难度」「营养」.
- Numbered steps: each number is a 28px solid terracotta circle with a white digit; step
  text 15px ink with generous line spacing, so it reads like a cookbook page.
- Seasoning list: compact 13px rows, low visual weight.
- Near the bottom: a quote block with a 3px terracotta left border and one line of
  Chinese adjustment advice.
- Very bottom, quiet and small: one single row of three health dots — muted green, warm
  amber, muted green — each followed by 12px gray label 「钠 · 中」「糖 · 低」「脂肪 · 中」.
  This single row is the ONLY place other colors are allowed.
- The source line is 11px gray at the very bottom.
- NO medical report look: no full-grid tables, no red warnings, no warning triangles, no
  gauge charts, no big numeric readouts, no traffic-light panel, no chart, no sparkline.

BOTTOM STRIP of the sheet, 120px tall, completely flat: five solid color swatches with
labels #a96f4e / #F4F1EB / #FBF9F6 / #2E2A26 / #E6E0D8, and a type-scale sample showing
24 / 18 / 15 / 13 / 12 px.

Chinese text must be short, crisp and correctly rendered. Everything that is not in the
quoted string list must be drawn as soft gray placeholder bars, never as invented Chinese
characters.
```

## 三、负向提示词

```
3d mockup, phone bezel, notch, hands holding phone, perspective, isometric, drop shadow,
bevel, skeuomorphism, gradient, mesh gradient, noise, grain, paper texture, glassmorphism,
frosted glass, blur, translucency, neon, glow, bloom, lens flare, dark mode, purple, blue
accent, rainbow palette, cartoon, cute mascot, chibi, emoji, sticker, food photography,
plated dish, bokeh, warm light filter, orange glow, steam, progress bar, percentage,
countdown, timer, remaining time, spinner, loading ring, circular progress, pulse,
breathing dot, heartbeat, waveform, ECG, seismograph, equalizer, medical chart, lab report,
full grid table, red alert, warning triangle, exclamation badge, gauge, speedometer,
sparkline, chart, dashboard, KPI card
```

## 四、调用参数建议

- 尺寸：横向 **1536×1024**（两屏并排 + 底部 token 带需要横向空间）。若工具只支持 1024×1024，改用「两屏并排、去掉底部色带」的裁剪版，另出第二张纯色卡图。
- 出图张数：先出 2~3 张同提示词，挑版式最干净的一张；不要为了好看改提示词。
- 质检要点（照图写 CSS 前必须逐条核对）：① 全图是否还有任何渐变/发光残留；② 左屏是否出现进度条/转圈/心跳线；③ 右屏是否退化成全网格表格或红黄绿体检灯；④ 陶土色是否只出现在实心块和细线上。
