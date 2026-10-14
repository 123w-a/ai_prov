import { srcPage, srcTitle } from '../data/clean.ts'
import { Icon } from '../ui/Icon.tsx'

/**
 * 结果页右栏第四段「本轮依据」（2026-10-10 从 SideRail.tsx 拆出：该文件 163 行，
 * 越过 blocks 单文件 150 行上限）。选这一段的理由：它自带完整判据（确定性出处
 * 优先、一条都没有时退回模型自述并标注），与前三段的 vm 判据不共享状态。
 * 注释与实现逐字随迁，只为满足规模上限，不改行为。
 *
 * · 确定性出处（带 source 的护栏）优先：一条都没有时才退回 sources（模型自述），
 *   且必须明确标注"未与规则库核对"——把两者混排等于给出处造假。
 * · 两种形态都不渲染时返回 null（调用方已按"整块不渲染"闸过一遍）。
 */
export function RailEvidence({
  evidence,
  sources,
}: {
  evidence: Array<{ condition?: string; rule?: string; source: string }>
  sources: Array<{ source: string }>
}) {
  if (evidence.length > 0) {
    return (
      <section className="rail-sect" data-sect="evidence">
        <h2 className="rail-title">
          <Icon name="doc" />
          本轮依据
        </h2>
        <ul className="rail-sources">
          {evidence.map((g, i) => (
            <li key={i}>
              <span className="src-cond">{g.condition}</span>
              {/* rule 是这一栏的主句，必须显示：只有"条件 + 出处"时，两条同条件
                  的护栏（实测两条"孕期"）在右栏长得一模一样，读的人分不清哪条
                  是"水产要熟透"、哪条是"钠不超 2000mg"。 */}
              {g.rule && <span className="src-rule">{g.rule}</span>}
              {/* 名字单行截断、全名挂 title；页码另起一行完整可见（那是能直接翻书核对的东西）。 */}
              <span className="src-name" title={g.source}>
                {srcTitle(g.source)}
              </span>
              {srcPage(g.source) && <span className="src-page">{srcPage(g.source)}</span>}
            </li>
          ))}
        </ul>
        <p className="rail-src-note">出自国家食养指南，可逐条核对。</p>
      </section>
    )
  }
  if (sources.length === 0) return null
  return (
    <section className="rail-sect" data-sect="evidence">
      <h2 className="rail-title">
        <Icon name="doc" />
        本轮依据
      </h2>
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
    </section>
  )
}
