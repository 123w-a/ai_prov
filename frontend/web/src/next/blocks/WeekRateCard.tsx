/**
 * C 设计稿的卡 5「趋势（达标率）」。
 *
 * 本周那个大号百分比 + 「较上周 ↑29%」是**百分点差**（86% - 57% = +29pp），
 * 不是相对变化（+50.9%）。说「较上周 ↑29%」时用户读的是"从 57 涨到 86"，
 * 换成相对变化会变成「↑51%」——数字更大但没人这么读，反而像算错了。
 *
 * 上周零记录（`prev.rate === null`）时不写差、写「上周无记录」：
 * 拿 0 当基准会算出「↑86%」，那是把一个不存在的东西当成了地板。
 */
import { CardHead } from './WeekCardShell'
import { RateChart } from '../ui/RateChart'

export function WeekRateCard({
  rate,
  series,
  prev,
}: {
  rate: number | null
  series: Array<{ label: string; rate: number | null; meals: number; onTarget: number; isCurrent: boolean }>
  prev: { meals: number; onTarget: number; rate: number | null } | null
}) {
  if (rate === null) return null
  const pct = Math.round(rate * 100)
  let deltaLine = '上周无记录，这一周先立个基准'
  if (prev && prev.rate !== null) {
    const pp = Math.round((rate - prev.rate) * 100)
    if (pp === 0) deltaLine = `较上周 持平（${Math.round(prev.rate * 100)}%）`
    else deltaLine = pp > 0 ? `较上周 ↑ ${pp}%` : `较上周 ↓ ${-pp}%`
  }
  return (
    <section className="wk-card is-rate">
      <CardHead n="5" title="趋势" note="（达标率）" />
      <div className="wk-card-chart">
        <RateChart series={series} />
      </div>
      <p className="wk-card-sum2">
        本周达标率 <b>{pct}%</b>
      </p>
      <p className="wk-card-sub">{deltaLine}</p>
    </section>
  )
}
