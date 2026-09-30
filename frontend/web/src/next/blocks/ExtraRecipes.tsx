import type { Recipe } from '../data/viewModel.ts'
import { renderRichText } from '../../utils/richText.tsx'
import { Fold } from '../ui/Fold.tsx'

/**
 * 另外几道：主菜之外后端给的其余菜谱，收进折叠。
 *
 * 降级在调用方（`isShown(vm.rest)` 并 narrow），块只收 `recipes: Recipe[]`。
 *
 * `(r.steps ?? []).length > 0` 是**保留的原样空值兜底**：它是后端字段缺失
 * 的容忍，不是"这条要不要显示"的存在性降级——实测 `full` 夹具之外的
 * rest 数组里确实有缺 steps 的条目，直接 `.length` 会炸。这处按方案第五节
 * 属于 viewModel 该统一收口的事，与纯搬移分开做。
 */
export function ExtraRecipes({ recipes }: { recipes: Recipe[] }) {
  return (
    <Fold summary="另外几道" count={recipes.length}>
      {recipes.map((r, i) => (
        <article className="recipe-extra" key={i}>
          <h3>{r.name}</h3>
          {r.intro && <p>{r.intro}</p>}
          {(r.steps ?? []).length > 0 && (
            <ol className="steps is-compact">
              {(r.steps ?? []).map((s, j) => (
                <li key={j}>
                  <span className="step-no" aria-hidden="true">
                    {j + 1}
                  </span>
                  <span className="step-text">{renderRichText(s)}</span>
                </li>
              ))}
            </ol>
          )}
        </article>
      ))}
    </Fold>
  )
}
