import { formatStamp } from '../data/model.ts'
import { isShown, type ResultVM } from '../data/viewModel.ts'
import { Icon } from '../ui/Icon.tsx'
import { Panel } from '../ui/Panel.tsx'
import { RailEvidence } from './RailEvidence.tsx'
import { SafetyNotice } from './SafetyNotice.tsx'

/**
 * 常驻右栏（2026-10-08 照预期图 result-expect.png 重排为四段）。
 *
 * ═══ 段序照图 ═══════════════════════════════════════════════════════════
 *   这次搭配的关键        ← chef_tip（23/23 有数据），原中区「这一碗的重点」迁入
 *   {成员} 适配提示 ×N    ← dish_matrix / member_adjustments，原中区家人两列迁入
 *   注意事项              ← 护栏 + 营养灯（安全项，护栏排灯前）
 *   本轮依据              ← 带出处的护栏；一条都没有时退回模型自述并标注
 * 每段自判存在性：空段不渲染，数据少时右栏优雅缩短，绝不出现空圆角框。
 * 中区不再有这四段的副本（Sources 已同源去重）——同一句话一屏只印一遍。
 *
 * 有意保留与预期图的两处差异（内容忠实优先于标题忠实）：
 *   · 第四段叫「本轮依据」不叫「数据来源」：这一段列的是"条件 + 规则 + 出处 + 页码"
 *     的可逐条核对链，换成「数据来源」会把"能拿去翻书核对"这层语义抹平。
 *   · 成员段标题用真名字「{成员} 适配提示」，不补身份括注（孕中期/增肌期）：
 *     answer 里没有身份字段，编一个就是替档案说话。
 *
 * ═══ 不变的纪律 ═════════════════════════════════════════════════════════
 * · 只放后端真的给得出的东西。health_lights 的 label 是「钠/糖/脂肪」这类营养素，
 *   后端从不产出"某成员能不能吃"——成员段只吃 dish_matrix/member_adjustments，
 *   绝不拿营养灯改写成成员判断。
 * · 护栏排在灯光前（安全项必须最先被看见）。
 * · 模型自述只在一条确定性出处都没有时退回显示，且明确标注"未与规则库核对"。
 * · 「整块不渲染」的判断在调用方（任一段有数据才挂载）；块内只按段自判，
 *   空 Panel 不会被画出来。
 */
export function SideRail({ vm, sources, askedAt }: {
  vm: ResultVM
  sources: Array<{ source: string }>
  /** 本轮询问时刻（回声条同款）。列尾 meta 用；无时刻不渲染（AskEcho 纪律 3）。 */
  askedAt?: number | null
}) {
  // 只收真的带出处的护栏：空串表示"本产品给不出处"（过敏原、成员冲突、
  // 档案不可用这三类没有对应规则），把它们摆进"依据"栏就是一格空气。
  const evidence = vm.guardrails.filter((g) => g.source !== '')
  const members = isShown(vm.members) ? vm.members.data : null
  return (
    <aside className="result-rail">
      {/* 外壳撑满整行高度、内部这层 sticky（2026-10-04 的结构保留）：
          四段之后右栏变长，超过一屏时 sticky 自然失效、跟随滚动，行为仍正确。 */}
      <div className="rail-stick">
      <Panel variant="filled">
        {/* ── 段一：这次搭配的关键 ─────────────────────────────────── */}
        {isShown(vm.chefTip) && (
          <section className="rail-sect" data-sect="key">
            <h2 className="rail-title">
              <Icon name="leaf" />
              这次搭配的关键
            </h2>
            <p className="rail-key">{vm.chefTip.data}</p>
          </section>
        )}

        {/* ── 段二：每位家人的适配提示（真数据才出段） ───────────────── */}
        {members &&
          members.order.map((name) => (
            <section className="rail-sect" data-sect="member" key={name}>
              <h2 className="rail-title">
                <Icon name="people" />
                {name} 适配提示
              </h2>
              {members.lines[name].map((l) => (
                <p className="rail-mline" key={l.key}>
                  {l.verdict && <b className="member-verdict">{l.verdict}</b>}
                  {l.dish && <span className="member-dish">{l.dish}</span>}
                  {l.body && <span className="member-reason">{l.body}</span>}
                </p>
              ))}
            </section>
          ))}
        {members && members.orphans.length > 0 && (
          <section className="rail-sect" data-sect="member">
            <h2 className="rail-title">其他</h2>
            {members.orphans.map((t, i) => (
              <p className="rail-mline" key={i}>
                <span className="member-reason">{t}</span>
              </p>
            ))}
          </section>
        )}

        {/* ── 段三：注意事项（护栏在前——安全项最先被看见） ─────────── */}
        {isShown(vm.notice) && (
          <section className="rail-sect" data-sect="notice">
            <h2 className="rail-title">
              <Icon name="alert" />
              注意事项
            </h2>
            <ul className="lights">
              <SafetyNotice items={vm.guardrails} />
              {vm.ranked.map((l, i) => (
                <li key={i} className={`light is-${l.level}`}>
                  <b className="light-label">{l.label}</b>
                  {isShown(l.reason) && <span className="light-reason">{l.reason.data}</span>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* ── 段四：本轮依据（确定性出处优先，模型自述标注后兜底） ─────
            2026-10-10 拆到 ./RailEvidence.tsx（规模上限）。 */}
        <RailEvidence evidence={evidence} sources={sources} />
      </Panel>
      {/* 列尾 meta（2026-10-09 · lave2 解 A，用户已批）：四段卡组收在 Panel
          底，下面那片同层纸带缺的不是高度、是终止符。时间取回声条同款
          askedAt，无时刻不渲染（时间是事实，猜一个是错的）。 */}
      {askedAt ? (
        <p className="rail-meta">本轮 · {formatStamp(askedAt, Date.now())}</p>
      ) : null}
      </div>
    </aside>
  )
}
