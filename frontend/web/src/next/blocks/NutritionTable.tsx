import { isShown, type ResultVM } from '../data/viewModel.ts'

/**
 * 这顿的营养数值表（2026-10-08 照预期图 result-expect.png 改成三列表格）。
 *
 * 形态：项目 ｜ 含量 ｜ 每日参考值。第三列是 **NRV%（营养素参考值）**——
 * 基准取 GB 28050 固定值（能量 2000 kcal、蛋白质 60 g、脂肪 60 g、碳水 300 g、
 * 膳食纤维 25 g、钠 2000 mg），换算是标准行为不是补造数据；没有基准的键写「—」。
 * 百分比只回答「这一顿占一日参考值几成」，原始数值一个不改。
 *
 * 位置：图 ｜ 简介+本表 的右列（经 DishHero 的 children 落位），不再通栏铺开。
 *
 * 三档状态各自的样子（口径交代一句都不能省，否则半样的数字会被当成整顿的热量）：
 *   complete    六行表格
 *   partial     六行表格 + 口径「已覆盖食材小计」+ 一行缺口（未收录谁、谁缺克数）
 *   unavailable 不排表，只给一句「算不出营养数值：<原因>」——藏掉会把
 *               「有食材但正文没写用量」变成「这页没有营养信息」，后者不是用户能改的。
 *
 * 状态用 [data-status] 承载：三档的差别是"这张表算什么口径"，是数据属性不是样式变体。
 */
const NRV: Record<string, number> = {
  energy_kcal: 2000,
  protein_g: 60,
  fat_g: 60,
  carb_g: 300,
  fiber_g: 25,
  sodium_mg: 2000,
}

/** value 已是 viewModel 格式化好的纯数字串（fmtNutrient），Number 直接可用。 */
function nrvPct(key: string, value: string | null): string {
  if (value === null) return '—'
  const base = NRV[key]
  const num = Number(value)
  if (!base || !Number.isFinite(num)) return '—'
  return `${Math.round((num / base) * 100)}%`
}

export function NutritionTable({ vm }: { vm: ResultVM }) {
  if (!isShown(vm.nutritionFacts)) return null
  const n = vm.nutritionFacts.data
  if (n.rows.length === 0 && !n.empty) return null

  return (
    <section className="nutri" data-status={n.status}>
      <div className="nutri-head">
        <h2 className="nutri-title">营养参考</h2>
      </div>

      {n.rows.length > 0 ? (
        <table className="nutri-table">
          <thead>
            <tr>
              <th scope="col">项目</th>
              <th scope="col">含量</th>
              <th scope="col">每日参考值</th>
            </tr>
          </thead>
          <tbody>
            {n.rows.map((row) => (
              <tr
                key={row.key}
                className="nutri-row"
                data-unknown={row.value === null ? 'true' : undefined}
              >
                <th scope="row" className="nutri-label">
                  {row.label}
                </th>
                {/* 没查到就是「未收录」，绝不写 0：0 克膳食纤维是一句结论，「未收录」是一句实话。 */}
                <td className="nutri-value">
                  {row.value ?? '未收录'}
                  {row.value !== null && <span className="nutri-unit"> {row.unit}</span>}
                </td>
                <td className="nutri-pct">{nrvPct(row.key, row.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="nutri-empty">{n.empty}</p>
      )}

      {n.rows.length > 0 && n.exercise && (
        <p className="nutri-exercise">
          <span className="nutri-exercise-kcal">这顿约 {n.exercise.kcal} 千卡</span>
          {n.exercise.items.length > 0 && (
            <span className="nutri-exercise-time">
              {n.exercise.items.map((item) => `${item.label} ${item.minutes} 分钟`).join(' · ')}
            </span>
          )}
          {n.exercise.basis && <span className="nutri-exercise-basis">{n.exercise.basis}</span>}
        </p>
      )}

      {/* 口径与 NRV 出处压在最弱一级：它们是这张表的读数条件，不与数值抢注意力。 */}
      {n.rows.length > 0 && (
        <p className="nutri-basis">
          {n.basis}
          {n.basis ? ' · ' : ''}
          % 为营养素参考值（NRV，GB 28050）
        </p>
      )}
      {n.gaps && n.rows.length > 0 && <p className="nutri-gaps">{n.gaps}</p>}
    </section>
  )
}
