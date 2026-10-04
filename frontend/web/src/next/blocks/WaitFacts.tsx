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
        {fridge.length > 0 && (
          <section className="wait-fact">
            <h3 className="wait-fact-label"><Icon name="fridge" />冰箱现有</h3>
            <ul className="wait-chips">
              {fridge.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
        )}
        {facts && (
          <section className="wait-fact">
            <h3 className="wait-fact-label">家庭档案</h3>
            {facts.members.map((line) => (
              <p className="wait-fact-row" key={line}>
                {line}
              </p>
            ))}
            {facts.shared && <p className="wait-fact-shared">{facts.shared}</p>}
          </section>
        )}
        <p className="wait-caveat">以上只表示系统已经知道什么，菜单尚未确定。</p>
      </Panel>
    </div>
  )
}
