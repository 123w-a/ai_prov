/**
 * 书写面：输入框 + 提交按钮。
 *
 * 抽成组件的理由不是"少写几行 JSX"，而是**两处行为不能各自漂移**。
 * 首屏（AskView）与结果页底部（RunResult）现在都要有它；各写一份的代价是
 * 回车快捷键、禁用条件、按钮文案、placeholder 会在某一次改动里只同步一半，
 * 用户看到的就是"这一页的输入框跟刚才那个不一样"——那种不一致很难被复现，
 * 却每天都在损耗信任。
 *
 * 职责边界与 AskView 一致：**只渲染，不做任何 I/O**。发送、中断、存档全在 app 层。
 *
 * 两个形态的差别只有权重，不是两套组件：
 *   starter   首屏那一档：有标签「你想吃什么」、两行、提示「一句话就够」
 *   followup  结果页底部那一档：无标签、一行、提示指向"下一句话"，
 *             按裁决做成低权重静态书写面（分隔线见 styles/composer.css）
 */
export function AskComposer({
  text,
  onText,
  onSend,
  running,
  blocked,
  variant = 'starter',
  submitLabel,
}: {
  text: string
  onText: (next: string) => void
  onSend: () => void
  running: boolean
  blocked: boolean
  variant?: 'starter' | 'followup'
  /** 覆盖提交按钮文案。首屏失败态要写「重试」——同一个输入框，性质不同。 */
  submitLabel?: string
}) {
  const isFollowup = variant === 'followup'
  return (
    <div className={`composer${isFollowup ? ' is-followup' : ''}`}>
      <label className="ask">
        {!isFollowup && <span className="ask-label">你想吃什么</span>}
        {/* 稿纸语汇（上下发丝线 + 左侧边距线 + 横向格线）画在这一层及其伪元素上，
            textarea 本身一字符未改。结果页那一档**不再**关掉它——见 styles/base.css
            与 styles/composer.css 里两轮改写的理由。 */}
        <span className="ask-field">
          <textarea
            className="ask-input"
            value={text}
            rows={isFollowup ? 1 : 2}
            disabled={running}
            placeholder={isFollowup ? '还想调整或换一道？直接说就行' : undefined}
            onChange={(e) => onText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onSend()
            }}
          />
        </span>
      </label>

      <div className="ask-actions">
        <span className="ask-hint">
          {isFollowup
            ? '接着说完就发：换一道、改口味、加减食材都行。'
            : '一句话就够。小膳管家会结合家人的健康情况来定这一顿。'}
        </span>
        <button
          className="ask-go"
          type="button"
          disabled={blocked || !text.trim()}
          onClick={() => onSend()}
        >
          {submitLabel ?? (isFollowup ? '继续' : '开始')}
        </button>
      </div>
    </div>
  )
}


