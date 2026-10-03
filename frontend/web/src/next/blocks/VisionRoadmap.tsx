import type { ServiceVision } from '../../types'

/**
 * 愿景块：上门私厨的规划页（服务房分区一）。
 *
 * 只渲染接口给的字段，一个字不添：能力、路线图、远期依赖、隐私说明。
 * 路线图的 status 原值（done / partial / …）直接给用户看——那是后端的口径，
 * 翻译成中文容易把「partial」美化成「已完成」。语义色只落在圆点和状态字上。
 */
export function VisionRoadmap({ v }: { v: ServiceVision }) {
  return (
    <>
      <p className="sv-summary">{v.summary}</p>

      <div className="sv-k">当前能力</div>
      <ul className="sv-caps">
        {v.current_capabilities.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>

      <div className="sv-k">路线图</div>
      <ol className="sv-road">
        {v.roadmap.map((r) => (
          <li key={r.phase} data-status={r.status}>
            <div className="sv-road-h">
              <span className="sv-dot" aria-hidden />
              <span className="sv-road-phase">阶段 {r.phase}</span>
              <span className="sv-road-title">{r.title}</span>
              <span className="sv-road-st">{r.status}</span>
            </div>
            <p className="sv-road-desc">{r.description}</p>
          </li>
        ))}
      </ol>

      {v.future_dependencies.length > 0 && (
        <>
          <div className="sv-k">远期依赖</div>
          <ul className="sv-caps">
            {v.future_dependencies.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        </>
      )}

      <p className="sv-privacy">{v.privacy_note}</p>
    </>
  )
}
