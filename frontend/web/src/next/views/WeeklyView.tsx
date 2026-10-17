/**
 * 周报页（C 设计稿结构，2026-10-15 按稿重排）。
 *
 * 版面骨架照 `design-C-weekly-overview.png`：
 *
 *   header          标题 + 日期范围 + 一句口径
 *   信息条          多位成员一起吃的餐按共同要求判定
 *   区「家庭整体（本周）」
 *     编号卡片 1 本周餐次 / 2 营养达标情况 / 5 趋势
 *              3 常做的菜 Top 5 / 4 护栏提示 / 6 可执行建议
 *   区「家庭成员（本周）」  ← 成员卡（可点进详情抽屉）
 *   页脚            达标依据说明
 *
 * 重排前是「hero 营养灯 + 两列独立流」，那是"方向稿"读法下我自己排的版；
 * 用户对照设计稿后指出不一致，这次按稿做。原有内容一块没丢：
 *   · 逐营养素灯 → 卡 2 的点阵（改成一餐一点，见 WeekStatCards 的说明）；
 *   · 常做的菜 / 逐餐明细 → 卡 3，交互与展开态一并搬进 WeekDishCard；
 *   · 护栏 + 反馈 → 卡 4；建议 → 卡 6；
 *   · 逐营养素趋势线（SparkLine）→ **退场**：C 稿的趋势是"达标率随周变化"，
 *     而卡 4 已经逐项说了哪个营养素没到绿灯，两张图放一起是同一件事说两遍。
 *     SparkLine 本身留着，成员详情抽屉还在用它。
 *
 * 成员区与家庭区**同级**：家庭不是"默认视图"、成员也不是它的下钻。
 */
import { useCallback, useState } from 'react'
import { rangeLabel, useMemberWeekly, useWeeklyReport } from '../data/firstScreen.ts'
import { MemberWeekCards } from '../blocks/MemberWeekCards.tsx'
import { MemberWeekDrawer } from '../blocks/MemberWeekDrawer.tsx'
import { WeekMealCount, WeekLights } from '../blocks/WeekStatCards.tsx'
import { WeekRateCard } from '../blocks/WeekRateCard.tsx'
import { WeekDishCard } from '../blocks/WeekDishCard.tsx'
import { WeekGuardCard, WeekAdviceCard } from '../blocks/WeekTipCards.tsx'

export default function WeeklyView() {
  const { view, loading, error } = useWeeklyReport()
  const [openMember, setOpenMember] = useState<string | null>(null)
  const member = useMemberWeekly(openMember)
  const closeMember = useCallback(() => setOpenMember(null), [])

  if (loading) {
    return (
      <section className="wk" data-room="weekly">
        <p className="wk-loading">正在汇总这一周…</p>
      </section>
    )
  }

  if (error || !view) {
    return (
      <section className="wk" data-room="weekly">
        <p className="wk-empty">读不到这一周的数据：{error || '未知原因'}</p>
      </section>
    )
  }

  if (view.isEmpty) {
    return (
      <section className="wk" data-room="weekly">
        <p className="wk-empty">{view.message}</p>
      </section>
    )
  }

  const pick = (name: string) => {
    window.dispatchEvent(new CustomEvent('dish-pick', { detail: name }))
    window.location.hash = 'room=tonight'
  }

  return (
    <section className="wk" data-room="weekly">
      <header className="wk-head">
        <span className="wk-head-seal" aria-hidden="true" />
        <div className="wk-head-txt">
          <h2 className="wk-h">周报</h2>
          <p className="wk-sub">{rangeLabel(view.range) ?? '本周'}</p>
          <p className="wk-sub2">本周数据基于家庭成员的共同饮食记录</p>
        </div>
        {/* 右上信息条（C 设计稿）：把"多人同桌怎么判达标"这条口径摆在最上面。
            它是我之前一直列在待办里没做的那块，也是多人共餐落地后最容易
            被误读的一件事——数字看着比独食低，不是吃得差，是判得严。 */}
        <aside className="wk-note-top">
          <span className="wk-note-top-ic" aria-hidden="true">
            ⓘ
          </span>
          <span>
            多位成员一起吃的餐，
            <br />
            其达标按这几位共同要求判定
          </span>
        </aside>
      </header>

      <div className="wk-zone">
        <h3 className="wk-zone-t">家庭整体（本周）</h3>
        {/* 三列各自独立堆叠（不是网格的两行）：
              列 1  卡 1 本周餐次   → 卡 4 护栏提示
              列 2  卡 2 营养达标   → 卡 3 常做的菜
              列 3  卡 5 趋势       → 卡 6 可执行建议
            为什么不是"1 2 5 一行 / 3 4 6 一行"：那会让第一行强行对齐，
            卡 1(151px) 与卡 2(169px) 到卡 5(321px) 之间必然裂出 184px / 166px
            两个大空洞，而卡 3 被压在最窄列里、菜名只剩 59px。C 设计稿本来就是
            三列独立流，卡 3 接着卡 1 往下长。
            编号仍按稿子（1,2,5 / 3,4,6 的乱序也是稿子原样）——编号是内容标签，
            不是位置序号；位置服从内容体量：卡 3 要放长菜名所以进中列，
            卡 4 内容短所以进窄列。 */}
        <div className="wk-cols">
          <div className="wk-col">
            <WeekMealCount meals={view.meals} memberCount={view.members.length} prev={view.prev} />
            <WeekGuardCard guardrail={view.guardrail} lights={view.lights} feedback={view.feedback} />
          </div>
          <div className="wk-col">
            <WeekLights mealsDetail={view.mealsDetail} />
            <WeekDishCard
              dishes={view.dishes}
              dishImages={view.dishImages}
              mealsDetail={view.mealsDetail}
              onPick={pick}
            />
          </div>
          <div className="wk-col">
            <WeekRateCard rate={view.rate} series={view.rateSeries} prev={view.prev} />
            <WeekAdviceCard recommendations={view.recommendations} />
          </div>
        </div>
      </div>

      {view.members.length > 0 ? (
        <div className="wk-zone is-crew">
          {/* 区标题与上面「家庭整体（本周）」同级同款——两个平级对象的标题
              长得不一样的话，读起来会以为成员区是家庭区的附属。
              「点一位看详情」放这里，不放卡内：它说的是这一整区怎么用。 */}
          <h3 className="wk-zone-t">
            家庭成员（本周）
            <em className="wk-zone-note">· 点一位看这一周的详情</em>
          </h3>
          <MemberWeekCards members={view.members} onOpen={setOpenMember} />
        </div>
      ) : null}

      <p className="wk-foot-note">
        提示：达标依据每位成员的个性化营养目标；多位成员一起吃的餐，按共同要求判定达标。
      </p>

      {openMember ? (
        <MemberWeekDrawer
          name={openMember}
          view={member.view}
          loading={member.loading}
          error={member.error}
          onClose={closeMember}
        />
      ) : null}
    </section>
  )
}


