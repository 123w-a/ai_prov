/**
 * 达标率折线（C 设计稿「5. 趋势（达标率）」那张图）。
 *
 * 与 `SparkLine` **不是一件事，别互相顶替**：SparkLine 画的是单营养素近 7 天的
 * 灯档位趋势，没有坐标轴、只求一眼看出走向；这里画的是**逐周达标率**，纵轴
 * 钉死 0–100%、节点上标出百分比、横轴是周区间。它要回答的是"这几周在往哪走"，
 * 所以**纵轴必须是真的**——一旦按数据自适应缩放，两条周报看起来一样陡，
 * 而实际上一条从 20% 爬到 25%、另一条从 70% 爬到 86%。
 *
 * 两个刻意的取舍：
 *
 *  1. **某周 `rate: null` 就在那里断线**（不连到 0%、也不插值）。后端早已把
 *     "那周没记录"表达成 null 而不是 0。若这里顺手补成 0，画出来的是一条
 *     "那周吃得极差"的假谷底——用户看到的是"曾经很差"，真相是"那周没记"。
 *  2. **纵轴刻度固定写 0/25/50/75/100**，不由数据决定。见上：固定的尺子才能
 *     让不同时间的两张图可比。
 *
 * 颜色一律走 CSS class（本层的色值字面量会被 check-layers 拦，且配色该只住一处）。
 */
type WeekPoint = { label: string; rate: number | null; meals: number; onTarget: number; isCurrent: boolean }

const W = 360
const H = 168
const PAD_L = 40
const PAD_R = 18
const PAD_T = 20
const PAD_B = 28

export function RateChart({ series }: { series: WeekPoint[] }) {
  if (series.length === 0) return null

  const innerW = W - PAD_L - PAD_R
  const innerH = H - PAD_T - PAD_B
  // 只有一格时把它放在中间，否则按格数等分——避免 x 轴除零。
  const stepX = series.length > 1 ? innerW / (series.length - 1) : 0
  const xOf = (i: number) => (series.length > 1 ? PAD_L + i * stepX : PAD_L + innerW / 2)
  const yOf = (rate: number) => PAD_T + innerH * (1 - rate)

  const pts = series.map((w, i) => ({
    x: xOf(i),
    y: w.rate === null ? null : yOf(w.rate),
    w,
  }))

  // 分段：连续的已知点连成一条线，遇到 null 就断开。
  // 只有一个点的"段"不画线（没有方向可言），但节点照画。
  const segs: string[] = []
  let cur: string[] = []
  for (const p of pts) {
    if (p.y === null) {
      if (cur.length > 1) segs.push(cur.join(' '))
      cur = []
    } else {
      cur.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    }
  }
  if (cur.length > 1) segs.push(cur.join(' '))

  const current = series.find((w) => w.isCurrent)

  return (
    <svg
      className="rate-chart"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={
        current && current.rate !== null
          ? `近 ${series.length} 周达标率，本周 ${Math.round(current.rate * 100)}%`
          : `近 ${series.length} 周达标率`
      }
    >
      {/* 纵轴：0/25/50/75/100 五条，固定的尺子 */}
      {[0, 0.25, 0.5, 0.75, 1].map((t) => {
        const y = yOf(t)
        return (
          <g key={t}>
            <line className="rate-grid" x1={PAD_L} y1={y} x2={W - PAD_R} y2={y} />
            <text className="rate-ytick" x={PAD_L - 6} y={y + 3} textAnchor="end">
              {Math.round(t * 100)}%
            </text>
          </g>
        )
      })}

      {/* 折线本体 */}
      {segs.map((seg) => (
        <polyline key={seg} className="rate-line" points={seg} fill="none" />
      ))}

      {/* 节点 + 百分比：有数才画。数值直接标在点上，省得读图去对纵轴 */}
      {pts.map((p, i) =>
        p.y === null ? null : (
          <g key={i}>
            <circle className="rate-dot" cx={p.x} cy={p.y} r={3} />
            <text className="rate-val" x={p.x} y={p.y - 8} textAnchor="middle">
              {Math.round((p.w.rate ?? 0) * 100)}%
            </text>
          </g>
        ),
      )}

      {/* 横轴标签：只留每一个周区间，本周加粗 */}
      {pts.map((p, i) => (
        <text
          key={`x${i}`}
          className={p.w.isCurrent ? 'rate-xtick is-current' : 'rate-xtick'}
          x={p.x}
          y={H - PAD_B + 16}
          textAnchor="middle"
        >
          {p.w.label}
        </text>
      ))}
    </svg>
  )
}
