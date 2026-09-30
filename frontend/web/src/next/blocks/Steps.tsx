import { renderRichText } from '../../utils/richText.tsx'
import { isShown, type ResultVM } from '../data/viewModel.ts'

/**
 * 做法（方案第二节块清单里的 Steps）：真实步数，不截断。
 *
 * 大号陶土编号是这一页唯一的"重"装饰，它承载的是动作顺序，不是奖励。
 * 实测 steps 是 3~8 条，截成固定 3 步就是丢真实内容。
 *
 * ── 降级归属 ────────────────────────────────────────────────
 * 「整块是否出现」由调用方判并 narrow（`isShown(vm.steps)`），块只收已展开的
 * `steps: string[]`——`Display<T>` 的 `.data` 只在 show 分支存在。
 * 块内只留一处 `isShown(seasonings)`，因为调料行是做法的子元素，
 * 它有无与做法是否渲染是两件事。按方案第五节这处最终也该交回 viewModel，
 * 那是改行为的独立一步，不与搬移混做。
 */
export function Steps({ steps, seasonings }: { steps: string[]; seasonings: ResultVM['seasonings'] }) {
  return (
    <section className="steps-block">
      <h2 className="section-title">做法</h2>
      {isShown(seasonings) && (
        <p className="seasoning-line">
          {seasonings.data.map((s, i) => (
            <span key={i} className="seasoning">
              {s.name}
              {s.amount && <b>{s.amount}</b>}
            </span>
          ))}
        </p>
      )}
      <ol className="steps">
        {steps.map((s, i) => (
          <li key={i}>
            <span className="step-no" aria-hidden="true">
              {i + 1}
            </span>
            <span className="step-text">{renderRichText(s)}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
