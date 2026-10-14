import { Panel } from '../ui/Panel.tsx'
import { Icon } from '../ui/Icon.tsx'
import type { familyFacts } from '../data/household.ts'

type Facts = ReturnType<typeof familyFacts>

/**
 * 等待页的「已知输入」证据面板（冰箱 + 家庭档案，2026-10-01 从 WaitCard 拆出）。
 *
 * 纪律（沿用文件头硬规矩第 3 条）：食材只作为"已知输入"的证据，
 * 绝不占主位、不排序、不配图、不出现任何菜名——否则用户会以为菜单已经定了。
 * 面板底部的 caveat 就是这条纪律的落点。
 */
export function WaitFacts({ facts, fridge }: { facts: Facts | null; fridge: string[] }) {
  if (!facts && fridge.length === 0) return null

  return (
    <div className="wait-facts">
      <Panel variant="outline">
        {/* 两路「已知输入」排成一张两列表：标签一列、值一列，行间一条发丝线。
            2026-10-07（预期图 B）：上一版是「标签在上 + 两段共用一根左竖线」的链式排版，
            那是给 620px 窄框画的；框放宽到通栏之后，竖线罩着一屏空框、字全挤在左下角，
            框本身成了空壳。表格把值挪到同一条起线上，也让宽度真的用起来。 */}
        <div className="wait-table">
          {fridge.length > 0 && (
            <div className="wait-fact">
              <h3 className="wait-fact-label"><Icon name="fridge" />冰箱现有</h3>
              <ul className="wait-chips">
                {fridge.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {facts && (
            <div className="wait-fact">
              <h3 className="wait-fact-label">家庭档案</h3>
              {/* 值列包一层：多行值若不包，第二个成员会被栅格排到下一行的标签列里去。 */}
              <div className="wait-fact-body">
                {facts.members.map((line) => (
                  <p className="wait-fact-row" key={line}>
                    {line}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
        {/* 「共同忌口」是冰箱 ∩ 档案求出来的**交集**（data/household.ts 用 sets.every 求的），
            不是家庭档案的一个属性。此前它渲染在档案段的内部、长得像那一节的尾巴，
            等于把这一屏最有力的一处结构藏了起来：两路依据收束成一条约束。
            现在它跳出链的缩进、不挂竖线、保留 --accent-ink，读作「收束」。 */}
        {facts?.shared && <p className="wait-fact-shared">{facts.shared}</p>}
        <p className="wait-caveat">以上只表示系统已经知道什么，菜单尚未确定。</p>
      </Panel>
    </div>
  )
}
