/**
 * 卡 2「本周吃饭记录」：7 天 × 早/午/晚 = **一格一顿**（2026-10-19 替换原 169 点阵）。
 *
 * 为什么换：点阵是"一条记录一个点"，而记账是**一道菜一笔**、营养灯判的是
 * **这一份菜谱的用料**。直接拿记录当"餐"，10-13 一天 48 条记录就是 48 个点，
 * 其中「宫保鸡丁（无花生·家庭减钠版）」占了 18 个——那不是吃了 18 顿。
 * 矩阵先把菜归并成顿（口径见后端 `settle_meals`），一格一顿，
 * 一眼能看出"是哪一顿吃咸了"，也留得住 21 格里那几格"没记录"。
 *
 * 四种格子，与设计稿一致：
 *   ✓ 达标（该顿全绿） / ~ 接近（有黄无红） / ! 超标（有红） / — 未记录
 *
 * **两种"—"必须分开说**（`records` 区分）：
 *   records === 0 → 这一顿没记录（不等于没吃）
 *   records  > 0 → 记了，但没给营养评估
 * 后者用虚框 + 「未评估」提示，绝不说成"没记录"，更不说成"没达标"。
 */
import { Fragment } from 'react'
import { CardHead } from './WeekCardShell.tsx'
import type { MealCell, MealGrid } from '../data/firstScreen.ts'

const GLYPH: Record<string, string> = { green: '✓', yellow: '~', red: '!' }

const LEVEL_TEXT: Record<string, string> = {
  green: '达标',
  yellow: '接近上限',
  red: '超标',
}

/** 格子的鼠标提示：把"哪一顿、几条记录、哪道菜哪一项"一次说全。 */
function cellTitle(cell: MealCell, day: string, slot: string): string {
  const head = `${day} ${slot}餐`
  if (cell.records === 0) return `${head} · 没有记录（不代表没吃）`
  if (!cell.level) return `${head} · ${cell.records} 条记录，未给营养评估`
  const bad = cell.bad.map((b) => `${b.dish}（${b.nutrient}）`).join('、')
  return `${head} · ${LEVEL_TEXT[cell.level]} · ${cell.records} 条记录${bad ? ` · 超标：${bad}` : ''}`
}

/** 「10-12」→「10/12」：矩阵右上角的日期范围用。 */
function slash(date: string): string {
  return date.slice(5).replace('-', '/')
}

export function WeekMealGrid({ grid }: { grid: MealGrid }) {
  const { days, slots, cells, legend } = grid
  if (days.length === 0 || slots.length === 0) return null
  const rated = legend.green + legend.yellow + legend.red
  return (
    <section className="wk-card is-meals">
      <div className="wk-meal-head">
        {/* 编号由 2 改 3（2026-10-19）：按三列的实际阅读顺序重排，见 WeekTipCards 的注释。 */}
        <CardHead n="3" title="本周吃饭记录" />
        <span className="wk-meal-range">
          {grid.range[0] ? slash(grid.range[0]) : ''} – {grid.range[1] ? slash(grid.range[1]) : ''}
        </span>
      </div>
      {/* 副标题照设计稿一句一行：「今天还没记录」放进 title 而不是正文——
          正文多那半句会把这一行挤成两行，把矩阵整体往下推 20px，
          而它本来就是"悬停才知道"级别的补充信息。 */}
      <p
        className="wk-card-sub"
        title={grid.droppedToday ? `今天 ${slash(grid.droppedToday)} 还没有记录` : undefined}
      >
        每格 = 一顿，最多 {grid.total} 格；空格表示没记录
      </p>
      {/* 图例由底部移到矩阵前面（2026-10-19）：✓ ~ ! — 四个符号在格子里出现，
          解释却排在整张卡的最后一行小字——看的时候早就看完了，只能靠猜。
          先给图例再给矩阵，读法才对得上。 */}
      <ul className="wk-meal-legend">
        <li data-c="green">✓ 达标 {legend.green} 顿</li>
        <li data-c="yellow">~ 接近 {legend.yellow} 顿</li>
        <li data-c="red">! 超标 {legend.red} 顿</li>
        <li data-c="none">— 未记录 {legend.none} 格</li>
      </ul>
      {/* 列宽由天数决定：窗口跨 7 天就是 21 格，跨 8 天就是 24 格。
          写死 7 会让最后一天整列消失——那是丢数据，不是排版。 */}
      <div
        className="wk-meal-grid"
        style={{ gridTemplateColumns: `26px repeat(${days.length}, minmax(0, 1fr))` }}
        role="table"
        aria-label={`本周吃饭记录：${rated} 顿有评估，${legend.none} 格没记录`}
      >
        <span className="wk-meal-corner" aria-hidden="true" />
        {days.map((d) => (
          <span className="wk-meal-th" key={`h-${d.date}`}>
            周{d.weekday}
          </span>
        ))}
        {slots.map((slot, i) => (
          <Fragment key={slot}>
            <span className="wk-meal-side">{slot}</span>
            {(cells[i] ?? []).map((cell, j) => {
              const day = days[j]
              if (!day) return null
              const empty = cell.records === 0
              const c = empty ? 'none' : cell.level ?? 'none'
              return (
                <span
                  className="wk-meal-cell"
                  data-c={c}
                  data-unevaluated={!empty && !cell.level ? 'y' : 'n'}
                  key={`${slot}-${day.date}`}
                  title={cellTitle(cell, `周${day.weekday}`, slot)}
                >
                  {empty ? '—' : cell.level ? GLYPH[cell.level] : '·'}
                </span>
              )
            })}
          </Fragment>
        ))}
      </div>
    </section>
  )
}
