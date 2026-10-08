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
 * ── 2026-10-07 下午：用料横排在上、步骤回到全宽 ──────────────────────
 * 上午照用户裁定的预期图 B 排成「左用料 260px / 右步骤」。用户看过真实页后判
 * 「结果页有问题，不好看」：实测真实步骤 6 条、每条 50~70 字，压到 386px 后约
 * 18~20 字/行，整块高约 790px，比左栏用料卡（约 450px）高一截——左短右长，
 * 而且步骤窄得难读。lave 第二意见同判：B 稿这个取舍的代价大于收益。
 *
 * 改法只动排布、不动信息：食材 / 调味料 各自成卡横排在步骤上方（窄屏自动落回
 * 竖排），步骤占满整栏。用料仍然独立成面——B 稿要解决的「克数没地方放」没被退回。
 * 没采纳 lave 的另一条建议（把用料搬进 278px 右栏）：右栏是 sticky 的，塞进十来行
 * 会让它总高超过一屏，滚起来反而看不见。
 *
 * ── 降级归属 ────────────────────────────────────────────────
 * 「整块是否出现」由调用方判并 narrow（`isShown(vm.steps)`），块只收已展开的
 * `steps: string[]`——`Display<T>` 的 `.data` 只在 show 分支存在。
 * 块内只留一处判断：两个 Display 都 hide 时**整行撤掉**，步骤直接顶到标题下，
 * 不留下一条空白的窄栏（后端只给步骤的情形实测存在）。
 */
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



