import type { FamilyMemberRow, WeekView } from '../data/firstScreen.ts'
import { Icon } from '../ui/Icon.tsx'
import { WeekBlock } from './WeekBlock.tsx'

/**
 * 首屏右栏四块（IA 薄切片契约 v2.1 §1/§2）。
 *
 * 阅读顺序按任务优先级递减：**待确认 → 家庭 → 本周 → 冰箱**（契约 §2，
 * 范围仅限桌面首屏，不覆盖结果页正文）。
 *
 * 四条硬纪律：
 *   1 **只读**。全程不碰任何写接口——B1 的确认/忽略由用户拍板延后到后续切片
 *      （契约 §9 W1）。本文件里**不得**出现 button / a[href] / input[type=submit]
 *      （lave §5.5 判据：B1 与右栏整体无操作控件）。
 *   2 **存在性判断在这里**（各块要不要出现由调用方定，块自己不猜），
 *      四块全空则整栏不渲染——绝不留一个空框。
 *   3 空值语义按契约 §1 分三类，不许合并：candidates/members/items 为空 ⇒ 隐藏；
 *      本周 has_data=false ⇒ **显示明确空态**（内容性空）；接口失败 ⇒ 隐藏且不留白。
 *   4 颜色一律 --ink / --ink-soft，**不用 --ink-faint**：小字号走 faint 正是
 *      对比度未过门禁的那一项（契约 §6 是硬阻塞），避它是守门禁，不是视觉升级。
 *
 * 2026-09-28：B3 本周块拆去 WeekBlock.tsx —— 四块挤一文件到 171 行，
 * 撞 check-layers 的 150 行上限。拆最大那块，其余留在这里。
 */
export function AskRail({
  fam,
  week,
  cand,
  fridge,
}: {
  fam: { members: FamilyMemberRow[]; shared: string[] } | null
  week: WeekView | null
  cand: { member: string; source: string; severity: string } | null
  fridge: { count: number; names: string } | null
}) {
  if (!cand && !fam && !week && !fridge) return null
  return (
    <aside className="ask-side">
      {cand && <CandidateBlock cand={cand} />}
      {fam && <FamilyBlock fam={fam} />}
      {week && <WeekBlock week={week} />}
      {fridge && <FridgeBlock fridge={fridge} />}
    </aside>
  )
}

/** B1 待确认候选（强度一 · 只读）。DOM 顺序：标题 → 原话 → 解释（lave §5.5 断言）。 */
function CandidateBlock({
  cand,
}: {
  cand: { member: string; source: string; severity: string }
}) {
  return (
    <section className="fs-block is-key" data-block="candidate">
      <h3 className="fs-title">待确认</h3>
      {/* 原话沉底单列：这条事项里唯一带时效性的信息，要能一眼读到原文。 */}
      <p className="fs-quote">{cand.source}</p>
      <p className="fs-note">
        {cand.member} 提出 · 确认与忽略将在后续接入
      </p>
    </section>
  )
}

/** B2 家庭档案（强度二）。一人一组 dt/dd，数字右对齐、条件/目标走描边小标签。 */
function FamilyBlock({ fam }: { fam: { members: FamilyMemberRow[]; shared: string[] } }) {
  return (
    <section className="fs-block" data-block="family">
      <h3 className="fs-title"><Icon name="people" />按谁的档案来定</h3>
      <dl className="fs-dl">
        {fam.members.map((m) => (
          <div key={m.id} className={`fs-mrow${m.isActive ? ' is-active' : ''}`}>
            <dt className="fs-mname">
              {m.name}
              {m.tags.map((t) => (
                <span key={t} className="fs-tag">
                  {t}
                </span>
              ))}
            </dt>
            <dd className="fs-mnum">{m.digits.join(' · ')}</dd>
          </div>
        ))}
        {fam.shared.length > 0 && (
          <div className="fs-mrow">
            <dt className="fs-mname">共同忌口</dt>
            <dd className="fs-mnum">{fam.shared.join('、')}</dd>
          </div>
        )}
      </dl>
    </section>
  )
}

/** B4 冰箱（强度三）。计数 + 名字，无图标、无装饰。 */
function FridgeBlock({ fridge }: { fridge: { count: number; names: string } }) {
  return (
    <section className="fs-block" data-block="fridge">
      <h3 className="fs-title"><Icon name="fridge" />冰箱</h3>
      <p className="fs-weekhead">
        <b className="fs-count">{fridge.count}</b>
        <span className="fs-unit">样</span>
        <span className="fs-fnames">{fridge.names}</span>
      </p>
    </section>
  )
}



