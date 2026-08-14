import type { ChefAnswer, GuardrailItem, Recipe, SourceRef } from '../types'
import { Icon } from './Icon'

const GUARD_LABEL: Record<string, string> = {
  pass: '已符合',
  warn: '需注意',
  adjusted: '已调整',
}

function isKeySeasoning(name: string): boolean {
  return /盐|油|糖|酱|蚝|醋|胡椒|辣椒/.test(name)
}

function ScoreMeter({ label, value }: { label: string; value: number }) {
  const score = Math.max(1, Math.min(5, Math.round(value) || 1))
  return (
    <div className="score-meter" aria-label={`${label} ${score} 星（满分 5 星）`}>
      <span>{label}</span>
      <div className="score-dots" aria-hidden="true">
        {Array.from({ length: 5 }, (_, index) => (
          <i key={index} className={index < score ? 'filled' : ''} />
        ))}
      </div>
      <strong>{score}.0</strong>
    </div>
  )
}

function RecipeSheet({
  recipe,
  index,
  fallbackImage,
  fallbackAiImage,
  imageNote,
}: {
  recipe: Recipe
  index: number
  fallbackImage?: string | null
  fallbackAiImage?: boolean
  imageNote?: string
}) {
  const imageUrl = recipe.image_url || fallbackImage
  const aiImage = recipe.image_ai_generated || fallbackAiImage

  return (
    <article className="recipe-sheet">
      <header className="recipe-hero">
        <div className="recipe-heading">
          <div className="recipe-kicker">
            <span>精选方案</span>
            <b>{String(index + 1).padStart(2, '0')}</b>
          </div>
          <h2>{recipe.name || '今日推荐'}</h2>
          {recipe.intro && <p>{recipe.intro}</p>}
          <div className="recipe-scores">
            <ScoreMeter label="上手难度" value={recipe.difficulty} />
            <ScoreMeter label="营养表现" value={recipe.nutrition} />
          </div>
        </div>

        <figure className={imageUrl ? 'recipe-visual' : 'recipe-visual no-image'}>
          {imageUrl ? (
            <img src={imageUrl} alt={`${recipe.name}成品图`} />
          ) : (
            <div className="image-placeholder">
              <Icon name="utensils" size={28} />
              <span>暂无可靠成品图</span>
            </div>
          )}
          {aiImage && <figcaption>AI 生成示意图 · 非真实成品照</figcaption>}
        </figure>
      </header>

      <div className="recipe-content">
        <section className="mise-en-place" aria-labelledby={`seasonings-${index}`}>
          <div className="section-label">
            <span>01</span>
            <div>
              <h3 id={`seasonings-${index}`}>调料备齐</h3>
              <p>重点调味已用暖色标出</p>
            </div>
          </div>
          {recipe.seasonings.length > 0 ? (
            <div className="seasoning-grid">
              {recipe.seasonings.map((seasoning, seasoningIndex) => (
                <div
                  key={`${seasoning.name}-${seasoningIndex}`}
                  className={isKeySeasoning(seasoning.name) ? 'seasoning-chip key' : 'seasoning-chip'}
                >
                  <strong>{seasoning.name}</strong>
                  <span>{seasoning.amount}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="section-empty">本方案没有列出额外调料。</p>
          )}
        </section>

        <section className="method-section" aria-labelledby={`steps-${index}`}>
          <div className="section-label">
            <span>02</span>
            <div>
              <h3 id={`steps-${index}`}>开火步骤</h3>
              <p>按后厨出单顺序完成</p>
            </div>
          </div>
          <ol className="method-timeline">
            {recipe.steps.map((step, stepIndex) => (
              <li key={stepIndex}>
                <span className="step-number">{String(stepIndex + 1).padStart(2, '0')}</span>
                <p>{step}</p>
              </li>
            ))}
          </ol>
        </section>

        {!imageUrl && imageNote && (
          <div className="image-note">
            <Icon name="image" size={18} />
            <p>{imageNote}</p>
          </div>
        )}
      </div>
    </article>
  )
}

function InlineEvidence({
  guards,
  sources,
}: {
  guards: GuardrailItem[]
  sources: SourceRef[]
}) {
  if (guards.length === 0 && sources.length === 0) return null

  return (
    <details className="inline-evidence">
      <summary>
        <span>
          <Icon name="shield" size={18} />
          查看本方案的健康护栏与依据
        </span>
        <b>{guards.length + sources.length}</b>
      </summary>
      <div className="inline-evidence-body">
        {guards.map((guard, index) => (
          <div key={`${guard.condition}-${index}`} className={`inline-guard ${guard.status}`}>
            <div>
              <strong>{guard.condition}</strong>
              <span>{GUARD_LABEL[guard.status] || guard.status}</span>
            </div>
            {guard.rule && <p>{guard.rule}</p>}
            {guard.reason && <small>{guard.reason}</small>}
          </div>
        ))}
        {sources.map((source, index) => (
          <div key={`${source.source}-${index}`} className="inline-source">
            <Icon name="book" size={16} />
            <div>
              <strong>{source.source}</strong>
              {source.snippet && <p>{source.snippet}</p>}
            </div>
          </div>
        ))}
      </div>
    </details>
  )
}

export function RecipeCard({ answer }: { answer: ChefAnswer }) {
  const recipes = answer.recipes ?? []
  const guards = answer.guardrails ?? []
  const sources = answer.sources ?? []
  if (recipes.length === 0) return null

  return (
    <div className="recipe-stack">
      {recipes.map((recipe, index) => (
        <RecipeSheet
          key={`${recipe.name}-${index}`}
          recipe={recipe}
          index={index}
          fallbackImage={index === 0 ? answer.image_url : undefined}
          fallbackAiImage={index === 0 ? answer.image_ai_generated : undefined}
          imageNote={index === 0 ? answer.image_note : undefined}
        />
      ))}

      {answer.chef_tip && (
        <aside className="chef-note">
          <span className="chef-note-icon">
            <Icon name="chef" size={22} />
          </span>
          <div>
            <strong>管家叮嘱</strong>
            <p>{answer.chef_tip}</p>
          </div>
        </aside>
      )}

      <InlineEvidence guards={guards} sources={sources} />
    </div>
  )
}
