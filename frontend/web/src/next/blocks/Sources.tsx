import type { GuardVM } from '../data/viewModel.ts'
import { srcPage } from '../data/clean.ts'
import { Fold } from '../ui/Fold.tsx'

/**
 * 本轮依据（正文里那份可展开的来源列表）。
 *
 * ── 2026-10-05 改挂确定性通道（第 E 项）────────────────────────────────
 * 这里原来渲染的是 answer 里那个 sources 字段（一组来源条目：文件名、章节、
 * 原文片段），那是**模型自己写的**：它填不填、填得对不对都不可控，实测多数轮次
 * 是空的，而 agent/graph.py 里那三处空 sources 全是兜底路径。（那个来源条目模型
 * 在后端只被定义过、从未被构造过。）
 * 现在改为渲染 guardrails 里带出处的那些条目——条件来自 _merged_conditions
 * （用户原话 + 档案声明，不走模型），出处来自 RULES[cond]["source"]，与这条
 * 结论的 message 取自同一个规则字典（graph.py:2228/2232 的注释就是为此写的）。
 *
 * ── 它和右栏那栏的分工 ────────────────────────────────────────────────
 * 右栏 208px 塞不下完整指南名，所以那边是**扫读**：名字单行截断（全名挂 title）、
 * 页码另起一行。这里是**逐条核对**：指南名给全、约束正文给全、页码另起一行。
 * 两处内容同源但完整度不同——这才让这个折叠有存在的理由；若只是把右栏那几行
 * 原样再抄一遍，它就该删掉而不是留下。
 *
 * snippet（原文引用）这一层没有了：guardrails 不带原文片段，我们也不替它编一段。
 *
 * 降级在调用方（evidence 非空才渲染）：块只收已就绪的列表。
 * `count` 只是标题上的「本轮依据 2」——它说的是"有几条依据"，不是"有多少字"。
 */
export function Sources({ evidence }: { evidence: GuardVM[] }) {
  return (
    <Fold summary="本轮依据" count={evidence.length}>
      <ul className="sources">
        {evidence.map((g, i) => (
          <li key={i}>
            <span className="src-cond">{g.condition}</span>
            {g.rule && <span className="src-rule">{g.rule}</span>}
            {/* 这里不截断：折叠打开就是为了看全名。 */}
            <span className="src-name">{g.source}</span>
            {srcPage(g.source) && <span className="src-page">{srcPage(g.source)}</span>}
          </li>
        ))}
      </ul>
    </Fold>
  )
}
