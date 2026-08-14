import { useEffect, useMemo, useState } from 'react'
import { fetchServiceVision, previewService } from '../api/client'
import type { ServicePreviewResult, ServiceVision } from '../types'
import { Icon } from './Icon'

const DEMO_RECIPES = ['番茄炒蛋', '青椒牛肉', '番茄鸡蛋面', '蒜蓉西兰花', '清蒸鲈鱼', '酸辣土豆丝']

const ROADMAP_LABEL: Record<string, string> = {
  done: '已完成',
  partial: '建设中',
  planned: '远期规划',
}

export function ServicePreview() {
  const [vision, setVision] = useState<ServiceVision | null>(null)
  const [recipe, setRecipe] = useState(DEMO_RECIPES[0])
  const [inventory, setInventory] = useState('鸡蛋 2 个、番茄 2 个、食用油')
  const [result, setResult] = useState<ServicePreviewResult | null>(null)
  const [loadingVision, setLoadingVision] = useState(true)
  const [calculating, setCalculating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void fetchServiceVision()
      .then((data) => {
        if (!cancelled) setVision(data)
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setError(requestError instanceof Error ? requestError.message : '无法加载服务规划')
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingVision(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const completion = useMemo(() => {
    if (!result || result.required_ingredients.length === 0) return 0
    const ready = result.required_ingredients.length - result.missing_ingredients.length
    return Math.max(0, Math.round((ready / result.required_ingredients.length) * 100))
  }, [result])

  const calculate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCalculating(true)
    setError('')
    try {
      const preview = await previewService({
        recipe_name: recipe,
        inventory_text: inventory,
        mode: 'text',
      })
      setResult(preview)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '食材缺口计算失败')
    } finally {
      setCalculating(false)
    }
  }

  return (
    <main className="service-page">
      <section className="service-hero">
        <div className="service-hero-copy">
          <span className="service-badge">
            <i aria-hidden="true" />
            FUNCTION PREVIEW · 功能预演
          </span>
          <h1>
            从“今晚吃什么”
            <br />
            到“<span>缺什么，我带来</span>”
          </h1>
          <p>
            当前版本已经能用文字清单稳定计算食材缺口。真实厨师预约、支付与派单仍是远期规划，界面会明确区分可用能力和产品愿景。
          </p>
          <div className="service-capabilities">
            <span>
              <Icon name="check" size={16} /> 文字食材匹配已可用
            </span>
            <span>
              <Icon name="warning" size={16} /> 暂不提供真实下单
            </span>
          </div>
        </div>
        <div className="service-illustration" aria-hidden="true">
          <div className="plate">
            <span className="plate-leaf one" />
            <span className="plate-leaf two" />
            <span className="plate-dot one" />
            <span className="plate-dot two" />
            <Icon name="concierge" size={44} />
          </div>
          <span className="orbit-label top">AI 决策</span>
          <span className="orbit-label right">食材补齐</span>
          <span className="orbit-label bottom">健康护栏</span>
        </div>
      </section>

      <section className="service-demo-grid">
        <form className="service-form" onSubmit={(event) => void calculate(event)}>
          <header>
            <span className="eyebrow">Live capability</span>
            <h2>食材缺口计算器</h2>
            <p>选择一道演示菜，再告诉我家里已经有什么。</p>
          </header>

          <label className="field-group">
            <span>目标菜品</span>
            <select value={recipe} onChange={(event) => setRecipe(event.target.value)}>
              {DEMO_RECIPES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>

          <label className="field-group">
            <span>现有食材</span>
            <textarea
              value={inventory}
              rows={5}
              onChange={(event) => setInventory(event.target.value)}
              placeholder="例如：西红柿 2 个、鸡蛋、葱、食用油"
            />
            <small>支持逗号、顿号或换行；会自动处理“西红柿 / 番茄”等常见别名。</small>
          </label>

          <button type="submit" className="service-submit" disabled={calculating || !inventory.trim()}>
            <span>{calculating ? '正在计算' : '计算食材缺口'}</span>
            <Icon name="spark" size={18} />
          </button>

          {error && (
            <p className="service-error" role="alert">
              <Icon name="warning" size={17} />
              {error}
            </p>
          )}
        </form>

        <section className={result ? 'service-result has-result' : 'service-result'} aria-live="polite">
          {!result ? (
            <div className="result-placeholder">
              <div className="result-placeholder-icon">
                <Icon name="utensils" size={28} />
              </div>
              <span>等待计算</span>
              <h2>一眼看清家里有多少、还差多少</h2>
              <p>结果来自后端确定性匹配，不会用模型猜测不存在的食材。</p>
            </div>
          ) : (
            <>
              <header className="result-header">
                <div>
                  <span className="eyebrow">Kitchen readiness</span>
                  <h2>{result.recipe_name}</h2>
                </div>
                <div className="readiness-score" style={{ '--progress': `${completion}%` } as React.CSSProperties}>
                  <strong>{completion}%</strong>
                  <span>备齐度</span>
                </div>
              </header>

              <div className="ingredient-columns">
                <div className="ingredient-column ready">
                  <div className="ingredient-column-title">
                    <Icon name="check" size={17} />
                    <span>
                      <strong>家里已有</strong>
                      {result.detected_from_text.length} 项
                    </span>
                  </div>
                  <div className="ingredient-tags">
                    {result.detected_from_text.map((item) => (
                      <span key={item}>{item}</span>
                    ))}
                    {result.detected_from_text.length === 0 && <small>没有识别到食材</small>}
                  </div>
                </div>

                <div className="ingredient-column missing">
                  <div className="ingredient-column-title">
                    <Icon name="warning" size={17} />
                    <span>
                      <strong>还需要</strong>
                      {result.missing_ingredients.length} 项
                    </span>
                  </div>
                  <div className="ingredient-tags">
                    {result.missing_ingredients.map((item) => (
                      <span key={item}>{item}</span>
                    ))}
                    {result.missing_ingredients.length === 0 && <small>食材已经齐全</small>}
                  </div>
                </div>
              </div>

              <div className="bring-list">
                <div>
                  <span className="bring-icon">
                    <Icon name="concierge" size={20} />
                  </span>
                  <div>
                    <strong>未来可由厨师带来</strong>
                    <p>{result.chef_can_bring.join('、') || '无需补充食材'}</p>
                  </div>
                </div>
                <span className="planned-pill">规划能力</span>
              </div>

              <div className="boundary-note">
                <Icon name="warning" size={18} />
                <p>{result.blocked_reason}</p>
              </div>
            </>
          )}
        </section>
      </section>

      <section className="roadmap-section">
        <header className="roadmap-heading">
          <div>
            <span className="eyebrow">Capability roadmap</span>
            <h2>产品不是一张“假下单页”</h2>
          </div>
          <p>{loadingVision ? '正在读取后端能力边界…' : vision?.summary}</p>
        </header>

        <ol className="roadmap-list">
          {(vision?.roadmap ?? []).map((item) => (
            <li key={item.phase} className={item.status}>
              <span className="roadmap-phase">0{item.phase}</span>
              <div className="roadmap-copy">
                <div>
                  <h3>{item.title}</h3>
                  <span>{ROADMAP_LABEL[item.status] || item.status}</span>
                </div>
                <p>{item.description}</p>
              </div>
            </li>
          ))}
          {!loadingVision && !vision && (
            <li className="roadmap-unavailable">
              <Icon name="warning" />
              后端规划接口暂不可用
            </li>
          )}
        </ol>

        {vision?.privacy_note && (
          <aside className="privacy-card">
            <Icon name="shield" size={21} />
            <div>
              <strong>隐私边界先于业务扩张</strong>
              <p>{vision.privacy_note}</p>
            </div>
          </aside>
        )}
      </section>
    </main>
  )
}
