import { renderRichText } from '../../utils/richText.tsx'
import { isShown, type ResultVM } from '../data/viewModel.ts'

/** 克数的写法：整数写「300g」，小数留一位写「12.5g」。
 *  不四舍五入到整十、不加别的字——正文没给克数时后端给的就是 null，那这里什么都不写。 */
function gram(g: number): string {
  const n = Number.isInteger(g) ? g : Math.round(g * 10) / 10
  return `${n}g`
}

/**
 * 做法（方案第二节块清单里的 Steps）：真实步数，不截断。
 *
 * 大号陶土编号是这一页唯一的"重"装饰，它承载的是动作顺序，不是奖励。
 * 实测 steps 是 3~8 条，截成固定 3 步就是丢真实内容。
 *
 * ── 2026-10-08 照预期图 result-expect.png：步骤改编号圆圈卡 ──────────────
 * 预期图画的是"每步一张卡、编号圆圈、卡并排"。结构照它，几何不照抄：
 * 稿子里每步正文只有十几个字，真实数据 50~70 字/步——一行排 6 张（每卡约 100px）
 * 会重蹈 10-07 上午 B 稿窄卡的覆辙（386px 已被用户判过「窄得难读」）。
 * 卡列用 auto-fill minmax(300px, 1fr)：672px 主栏自然落两列，窄屏自动单列，
 * 每卡 300px 起（≈20 字/行，与被否前的全宽行相比可读性不降级）。
 * 步骤文本形如「小标题：正文」时拆出标题行（≤16 字才拆，无冒号原样整句）。
 *
 * ── 用料横排在上（2026-10-07 下午裁定保留）──────────────────────────────
 * 食材 / 调味料 各自成卡横排在步骤上方（窄屏自动落回竖排）。
 * 独立成面不退回：克数与整句话用量（`约3小颗单晶冰糖, 只用于炒色`）有真实落点。
 *
 * ── 降级归属 ────────────────────────────────────────────────
 * 「整块是否出现」由调用方判并 narrow（`isShown(vm.steps)`），块只收已展开的
 * `steps: string[]`——`Display<T>` 的 `.data` 只在 show 分支存在。
 * 块内只留一处判断：两个 Display 都 hide 时**整行撤掉**，步骤直接顶到标题下，
 * 不留下一条空白的窄栏（后端只给步骤的情形实测存在）。
 */

/** 「小标题：正文」拆分。标题过长（>16 字）多半是正文本体，不拆——
 *  把半句话当标题会把步骤读成碎片。无冒号/含句号的整句原样给正文。 */
function splitStep(s: string): { head?: string; text: string } {
  const m = /^([^：:\n。]{1,16})[：:]\s*([\s\S]+)$/.exec(s.trim())
  return m ? { head: m[1], text: m[2] } : { text: s }
}
export function Steps({
  steps,
  seasonings,
  ingredients,
}: {
  steps: string[]
  seasonings: ResultVM['seasonings']
  ingredients: ResultVM['ingredients']
}) {
  const hasMise = isShown(ingredients) || isShown(seasonings)

  return (
    <section className="steps-block">
      <h2 className="section-title">做法</h2>
      {/* 两组各自成卡横排：只有一组时它自己占一格（不跟着整栏被拉宽，见 .steps-mise）。 */}
      {hasMise && (
        <div className="steps-mise-row">
          {isShown(ingredients) && (
            <div className="steps-mise">
              <h3 className="steps-mise-title">食材</h3>
              <ul className="steps-mise-list">
                {ingredients.data.map((it, i) => (
                  <li key={i}>
                    <span className="steps-mise-name">{it.name}</span>
                    {/* 克数只在后端真给了数字时才写：补一个常识克数等于替这道菜编用量。 */}
                    {typeof it.amount_g === 'number' && (
                      <b className="steps-mise-amount">{gram(it.amount_g)}</b>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {isShown(seasonings) && (
            <div className="steps-mise">
              <h3 className="steps-mise-title">调味料</h3>
              <ul className="steps-mise-list">
                {seasonings.data.map((s, i) => (
                  <li key={i}>
                    <span className="steps-mise-name">{s.name}</span>
                    {s.amount && <b className="steps-mise-amount">{s.amount}</b>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <ol className="steps">
          {steps.map((s, i) => {
            const { head, text } = splitStep(s)
            return (
              <li key={i}>
                <span className="step-no" aria-hidden="true">
                  {i + 1}
                </span>
                <span className="step-body">
                  {head && <b className="step-head">{head}</b>}
                  <span className="step-text">{renderRichText(text)}</span>
                </span>
              </li>
            )
          })}
      </ol>
    </section>
  )
}



