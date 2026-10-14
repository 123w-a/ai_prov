import { srcPage, srcTitle } from '../data/clean.ts'
import { isShown, type ResultVM } from '../data/viewModel.ts'
import { Icon } from '../ui/Icon.tsx'
import { Panel } from '../ui/Panel.tsx'
import { SafetyNotice } from './SafetyNotice.tsx'

/**
 * 常驻右栏（方案第二节块清单里的 SideRail）。
 *
 * 放在 DOM 最前：窄屏落回单列时它自然在最上面，红色项不会掉出首屏
 * （对抗席红线一/二）。
 *
 * 这一栏只放后端**真的给得出**的东西：营养判据、安全护栏、来源。
 * **不放「孕期女性可以吃 / 增肌男性可以吃」**——实测 health_lights 的
 * label 是「钠/糖/脂肪」这类营养素，后端从不产出"某个成员能不能吃"
 * 这个结论；照设计稿画就是替后端编一条安全判断，不可接受。
 *
 * ── 降级放在外面 ────────────────────────────────────────────────
 * 「整块不渲染」的判断留在调用方：空栏一旦渲染成一个明晃晃的空圆角框
 * （thin 形态实测如此），这条面板样式就变成缺陷。块自己不做存在性判断，
 * 它拿到 vm 与 sources 就该渲染；要不要出现由调用方定。
 *
 * ── 块内仍留的两处 `&&` ────────────────────────────────────────
 * `isShown(vm.notice)` 与 `sources.length > 0` 是历史写法，按方案第五节
 * 它们最终该由 viewModel 的 display 判断承担。那一步要改 viewModel 与
 * 单测，是改行为、不是搬移，所以**分开做**——混进搬移就无法用
 * 「DOM 逐字节不变」作门禁。
 */
export function SideRail({ vm, sources }: { vm: ResultVM; sources: Array<{ source: string }> }) {
  // 只收真的带出处的护栏：空串表示"本产品给不出处"（过敏原、成员冲突、
  // 档案不可用这三类没有对应规则），把它们摆进"依据"栏就是一格空气——
  // 用户会以为漏印了，而它其实永远不会被填上。
  const evidence = vm.guardrails.filter((g) => g.source !== '')
  return (
    <aside className="result-rail">
      {/* 2026-10-04：这一层是「撑满」与「跟随」分开实现的产物。右栏外壳撑满整行
          高度（并在左侧画出章节轨道），内部这一层才 sticky。原先只有一层、直接
          sticky，页面长 1524px 而右栏内容只有 615px 时，首屏右侧下方就空掉一大片
          ——用户说的「留白的地方太多了」有一半来自这里。装饰与定位全在 CSS，DOM
          只多这一层。 */}
      <div className="rail-stick">
      <Panel variant="filled">
        {isShown(vm.notice) && (
          <>
            <h2 className="rail-title"><Icon name="alert" />这顿要注意的</h2>
            <ul className="lights">
              {/* 护栏排在最前（2026-09-26 修正）。
                  原来它写在营养灯**之后**，直接违反红线——安全项必须最先被看见。
                  这个错误之所以一直没被发现，是因为实测 22/23 条 guardrails 为空，
                  只有那 1/23 的满数据答案才会暴露它；满数据夹具台一跑就现形。 */}
              <SafetyNotice items={vm.guardrails} />
              {vm.ranked.map((l, i) => (
                <li key={i} className={`light is-${l.level}`}>
                  <b className="light-label">{l.label}</b>
                  {isShown(l.reason) && <span className="light-reason">{l.reason.data}</span>}
                </li>
              ))}
            </ul>
          </>
        )}

        {/* 本轮依据（2026-10-05 改挂确定性出处）。
            原先这里直接列后端答案里那份 sources，而那条链是不可信的：
            它的类型在整个后端只有 1 个定义点、0 个构造点，graph 里三处空列表
            全在兜底路径，正常轮次由 LLM 按 schema 自由填，实测多为空。把模型
            自述渲染成"权威依据"比不显示更糟——用户没法分辨哪条能拿去核对。
            现在优先列 guardrails 的 source：它由 _build_guardrails 从
            domain/nutrition_rules.py 的 RULES 机械取出（与结论同一个字典），
            指南名与页码可以逐条对着原文查。
            只有当**一条确定性出处都没有**、而模型自述还在时，才退回显示它，
            并且明确标注"模型自述"——不能让它冒充依据。
            （注：本注释刻意不写"字段访问"那种点号写法，否则会被 check-layers
             的 e_读后端字段 规则当成 blocks 层直读后端字段。） */}
        {evidence.length > 0 ? (
          <>
            <h2 className="rail-title"><Icon name="doc" />本轮依据</h2>
            <ul className="rail-sources">
              {evidence.map((g, i) => (
                <li key={i}>
                  <span className="src-cond">{g.condition}</span>
                  {/* rule 是这一栏的主句，必须显示：只有"条件 + 出处"时，两条同条件
                      的护栏（实测两条"孕期"）在右栏长得一模一样，读的人分不清哪条
                      是"水产要熟透"、哪条是"钠不超 2000mg"。这句话才是"依据什么"
                      真正在说的东西，指南只是它的落款。 */}
                  {g.rule && <span className="src-rule">{g.rule}</span>}
                  {/* 名字单行截断、全名挂 title：208px 塞不下 50 字的出处，
                      但页码必须完整可见（那是能直接翻书核对的东西），
                      所以页码另起一行，不参与截断。 */}
                  <span className="src-name" title={g.source}>{srcTitle(g.source)}</span>
                  {srcPage(g.source) && <span className="src-page">{srcPage(g.source)}</span>}
                </li>
              ))}
            </ul>
            <p className="rail-src-note">出自国家食养指南，可逐条核对。</p>
          </>
        ) : (
          sources.length > 0 && (
            <>
              <h2 className="rail-title"><Icon name="doc" />本轮依据</h2>
              <ul className="rail-sources">
                {sources.map((s, i) => (
                  <li key={i}>
                    <span className="src-name">{srcTitle(s.source)}</span>
                  </li>
                ))}
              </ul>
              <p className="rail-src-note" data-tone="weak">
                模型自述，未与规则库核对。
              </p>
            </>
          )
        )}
      </Panel>
      </div>
    </aside>
  )
}





