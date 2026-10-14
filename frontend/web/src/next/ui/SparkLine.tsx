/**
 * 趋势 sparkline：7 点折线。null = 当天无观测 → 该点断开（多段 polyline，不插值）。
 *
 * 2026-10-15 从 `views/WeeklyView.tsx` 提到 `ui/`：成员详情抽屉（`blocks/` 层）
 * 也要画同一张图，而 blocks 按 check-layers 的分层契约 **不能** import views。
 * 搬出来而不是复制一份：两条趋势线必须是同一个画法，否则家庭版和成员版的线
 * 在断点处理、点半径上会慢慢分叉，而"哪条是对的"到时候说不清。
 *
 * 纯 props、不读数据、不含状态，所以放 ui 层没有任何依赖问题。
 */
export function SparkLine({ values, state }: { values: (number | null)[]; state: string }) {
  const W = 104
  const H = 36
  const P = 5
  const n = values.length
  const x = (i: number) => P + (i * (W - 2 * P)) / Math.max(1, n - 1)
  const y = (v: number) => H - P - v * (H - 2 * P)
  // 连续非 null 段拆成独立 polyline；单点段不画线（circle 单独渲染）。
  const segs: string[] = []
  let cur: string[] = []
  values.forEach((v, i) => {
    if (v == null) {
      if (cur.length > 1) segs.push(cur.join(' '))
      cur = []
    } else {
      cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    }
  })
  if (cur.length > 1) segs.push(cur.join(' '))
  return (
    <svg
      className="wk-spark"
      data-state={state}
      viewBox={`0 0 ${W} ${H}`}
      aria-hidden="true"
      focusable="false"
    >
      {segs.map((pts, i) => (
        <polyline key={i} points={pts} fill="none" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      ))}
      {values.map((v, i) =>
        v == null ? null : <circle key={i} cx={x(i)} cy={y(v)} r="2.4" />
      )}
    </svg>
  )
}
