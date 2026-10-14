/**
 * 达标进度圆环（C 设计稿成员卡上那个环）。
 *
 * 为什么是原语而不是 blocks 里的一段：周报页的成员卡与成员详情抽屉**都要**这个环
 * （卡片上是大号、详情里想复用同一口径）。放 `ui/` 是因为它们分属 `views/` 与
 * `blocks/` 两层——`blocks/` 不能 import `views/`，只有 `ui/` 是两边都能用的
 * 公共层（`SparkLine` 当初也是因为同一个原因挪到这里）。
 *
 * 三个刻意的取舍：
 *
 *  1. **颜色走 CSS，不走 props**：两条弧分别挂 `.ring-track` / `.ring-bar`，
 *     由样式表决定颜色。SVG 里写死颜色的话，`check-layers` 的色值字面量检查
 *     会拦（它只扫 `/styles/`），更重要的是配色就散到两个地方去了。
 *  2. **`value === null` 画虚线空圆 + 一道横杠，绝不画成 0%**：语义是"不知道"，
 *     画成 0% 的空环会被读成"一餐都没达标"。这与后端「分母为 0 返回 null」
 *     是同一条纪律的前后两半。
 *  3. **`<title>` 带真实百分比**：环本身是图形，读屏读不出来；文字判断必须同时
 *     存在，所以这里给一个 title，卡面上也另有一行数字（色点必须配文字判断，
 *     本项目的既有口径）。
 */
export function RingProgress({
  value,
  size = 92,
  title,
}: {
  /** 0..1；null = 没有可判定的进度（"待补"），画虚线空圆。 */
  value: number | null
  size?: number
  title?: string
}) {
  const stroke = 8
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const mid = size / 2

  if (value === null) {
    return (
      <svg
        className="ring is-blank"
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={title || '暂无数据'}
      >
        <title>{title || '暂无数据'}</title>
        <circle className="ring-track" cx={mid} cy={mid} r={r} fill="none" strokeWidth={stroke} />
        {/* 虚线空圆里那道横杠：图形上说明"这里本该有数，但还没有"，
            比一个纯空圆更不像"渲染坏了"。 */}
        <line
          className="ring-dash"
          x1={mid - r * 0.34}
          y1={mid}
          x2={mid + r * 0.34}
          y2={mid}
          strokeWidth={stroke * 0.5}
          strokeLinecap="round"
        />
      </svg>
    )
  }

  // 夹到 [0,1]：后端理论上给 0..1，但一个脏值不该画出超过整圈的弧。
  const clamped = value < 0 ? 0 : value > 1 ? 1 : value
  const percent = Math.round(clamped * 100)
  return (
    <svg
      className="ring"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={title || `达标 ${percent}%`}
    >
      <title>{title || `达标 ${percent}%`}</title>
      <circle className="ring-track" cx={mid} cy={mid} r={r} fill="none" strokeWidth={stroke} />
      <circle
        className="ring-bar"
        cx={mid}
        cy={mid}
        r={r}
        fill="none"
        strokeWidth={stroke}
        strokeLinecap="round"
        // 从 12 点方向起画：rotate(-90) 把 SVG 默认的 3 点起点转到正上方，
        // 与设计稿一致；不转的话进度从右边开始，读起来像被裁掉了一角。
        transform={`rotate(-90 ${mid} ${mid})`}
        strokeDasharray={`${c * clamped} ${c}`}
      />
    </svg>
  )
}
