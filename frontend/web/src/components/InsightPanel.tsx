import type { ChefAnswer } from '../types'
import { formatSourceSection } from '../utils/sourceFormat'
import { FeedbackSummary } from './FeedbackSummary'
import { Icon } from './Icon'

const STATUS_COPY: Record<string, string> = {
  pass: '已符合',
  warn: '需注意',
  adjusted: '已调整',
}

export function InsightPanel({ answer }: { answer: ChefAnswer | null }) {
  const guards = answer?.guardrails ?? []
  const sources = answer?.sources ?? []

  // 一句话结论横幅：warn 优先 > adjusted > 全 pass
  const warnCount = guards.filter((g) => g.status === 'warn').length
  const adjustedCount = guards.filter((g) => g.status === 'adjusted').length
  let banner: { cls: string; icon: 'check' | 'warning' | 'shield'; text: string } | null = null
  if (guards.length > 0) {
    if (warnCount > 0) {
      banner = { cls: 'warn', icon: 'warning', text: `${warnCount} 项需注意——请留意用量与搭配` }
    } else if (adjustedCount > 0) {
      banner = { cls: 'adjusted', icon: 'shield', text: `已自动调整 ${adjustedCount} 处硬禁忌，本轮方案合规` }
    } else {
      banner = { cls: 'pass', icon: 'check', text: '本轮推荐已通过全部健康护栏' }
    }
  }

  const statusIcon = (status: string) =>
    status === 'warn' ? 'warning' : status === 'adjusted' ? 'shield' : 'check'

  return (
    <aside className="insight-panel" aria-label="健康护栏与证据链">
      <header className="insight-header">
        <div>
          <span className="eyebrow">Decision trace</span>
          <h2>健康决策链</h2>
        </div>
        <span className={answer ? 'trace-status ready' : 'trace-status'}>
          <i aria-hidden="true" />
          {answer ? '本轮已审计' : '等待本轮'}
        </span>
      </header>

      {!answer ? (
        <div className="trace-empty">
          <div className="trace-orbit" aria-hidden="true">
            <Icon name="shield" size={30} />
          </div>
          <h3>先排除不适合，再决定吃什么</h3>
          <p>完成一次膳食决策后，健康标签、审计结论和权威出处会固定显示在这里。</p>
          <ol className="trace-steps">
            <li>
              <span>1</span>
              识别需求与长期偏好
            </li>
            <li>
              <span>2</span>
              检索营养知识依据
            </li>
            <li>
              <span>3</span>
              校验禁忌并输出方案
            </li>
          </ol>
        </div>
      ) : (
        <div className="insight-scroll">
          <section className="insight-section" aria-labelledby="guardrail-heading">
            <div className="insight-section-title">
              <span className="insight-section-icon guard-icon">
                <Icon name="shield" size={18} />
              </span>
              <div>
                <h3 id="guardrail-heading">本轮健康护栏</h3>
                <p>{guards.length > 0 ? `${guards.length} 项确定性审计` : '未触发健康标签'}</p>
              </div>
            </div>

            {banner && (
              <div className={`guardrail-banner ${banner.cls}`}>
                <Icon name={banner.icon} size={15} />
                <span>{banner.text}</span>
              </div>
            )}

            {guards.length > 0 ? (
              <div className="guardrail-cards">
                {guards.map((guard, index) => (
                  <article key={`${guard.condition}-${index}`} className={`guardrail-card ${guard.status}`}>
                    <header>
                      <strong>
                        <Icon name={statusIcon(guard.status)} size={11} />
                        {guard.condition}
                      </strong>
                      <span>{STATUS_COPY[guard.status] || guard.status}</span>
                    </header>
                    {guard.rule && <p>{guard.rule}</p>}
                    {guard.reason && <small>{guard.reason}</small>}
                  </article>
                ))}
              </div>
            ) : (
              <div className="insight-empty-line">
                <Icon name="check" size={17} />
                本轮未检测到需额外提示的健康约束
              </div>
            )}

            {guards.length === 0 && (
              <p className="guardrail-empty">
                在「家庭成员」中建档后，档案里的健康标签会自动触发护栏核查。
              </p>
            )}
          </section>

          <section className="insight-section" aria-labelledby="sources-heading">
            <div className="insight-section-title">
              <span className="insight-section-icon source-icon">
                <Icon name="book" size={18} />
              </span>
              <div>
                <h3 id="sources-heading">权威依据</h3>
                <p>{sources.length > 0 ? `${sources.length} 条知识库命中` : '本轮无引用'}</p>
              </div>
            </div>

            {sources.length > 0 ? (
              <div className="source-cards">
                {sources.map((source, index) => (
                  <article key={`${source.source}-${index}`} className="source-card">
                    {source.category && <span>{source.category}</span>}
                    <h4>{source.source}{source.section ? ` · ${formatSourceSection(source.source, source.section)}` : ''}</h4>
                    <p>{source.snippet || '该来源未附带命中片段。'}</p>
                  </article>
                ))}
              </div>
            ) : (
              <div className="insight-empty-line muted">
                <Icon name="book" size={17} />
                未涉及需引用的健康结论
              </div>
            )}
          </section>

          <div className="transparency-note">
            <Icon name="spark" size={17} />
            <p>健康审计由后端规则注入，不由模型自由编写；AI 图片也会明确标注。</p>
          </div>
        </div>
      )}
      <FeedbackSummary />
    </aside>
  )
}
