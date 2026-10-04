import { isShown, type ResultVM } from '../data/viewModel.ts'

/**
 * 这顿的营养数值（第 5 项）。
 *
 * 形态照用户裁定的预期图：一行六列，数值大、单位小，列间一根发丝竖线，
 * 左上「营养参考」+ 右侧一句口径说明。
 *
 * 与预期图**故意不同的一处**：标题旁边不写「（每份）」，写「按菜谱用量」。
 * 后端算的是菜谱给出的整份食材用量，标「每份」会暗示还有几份没算进去。
 *
 * 三档状态各自的样子：
 *   complete    六列数值
 *   partial     六列数值 + 口径改成「已覆盖食材小计」+ 一行缺口（未收录谁、谁缺克数）
 *   unavailable 不排六列（没有数值可排），只给一句「算不出营养数值：<原因>」
 *
 * 为什么 unavailable 也要渲染、而不是整块藏掉：藏掉之后用户看到的是「这页没有营养信息」，
 * 而事实是「有食材、但正文没写用量」——后者是他能改的，前者不是。
 * 让这块安静消失，等于把这个区别一起藏了。
 */
export function NutritionTable({ vm }: { vm: ResultVM }) {
  if (!isShown(vm.nutritionFacts)) return null
  const n = vm.nutritionFacts.data
  if (n.rows.length === 0 && !n.empty) return null

  return (
    <section className="nutri" data-status={n.status}>
      <div className="nutri-head">
        <h2 className="nutri-title">营养参考</h2>
        {n.basis && <span className="nutri-basis">{n.basis}</span>}
      </div>

      {n.rows.length > 0 ? (
        <ul className="nutri-grid">
          {n.rows.map((row) => (
            <li key={row.key} className="nutri-cell" data-unknown={row.value === null ? 'true' : undefined}>
              <span className="nutri-label">{row.label}</span>
              {/* 没查到就是「未收录」，绝不写 0：0 克膳食纤维是一句结论，「未收录」是一句实话。 */}
              <span className="nutri-value">{row.value ?? '未收录'}</span>
              <span className="nutri-unit">{row.value === null ? '' : row.unit}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="nutri-empty">{n.empty}</p>
      )}

      {n.gaps && n.rows.length > 0 && <p className="nutri-gaps">{n.gaps}</p>}
    </section>
  )
}
