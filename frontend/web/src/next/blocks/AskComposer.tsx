import { AskVoice } from './AskVoice.tsx'
import { AskPhoto } from './AskPhoto.tsx'
import { useCaretInsert } from '../data/caretInsert.ts'
import { enterIntent } from '../data/enterToSend.ts'

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
  /* 语音识别的字落到光标处（不覆盖草稿）、落位后焦点回输入框。
     时序细节全在 useCaretInsert 里；这里只把它挂到 textarea 上。 */
  const { taRef, insert } = useCaretInsert(text, onText)
  /* 两档的提示语提取成量：starter 档它要排到面板**上方**（2026-10-10 首屏方向稿
     用户全盘认可——稿里 hint 在标题下、面板上，不与按钮同排），followup 档维持
     原位（面板内动作行左侧）。同一句话不写两遍，避免两处文案各自漂移。 */
  const briefHint = '一句话就够。小膳管家会结合家人的健康情况来定这一顿。'
  const followHint = '接着说完就发：换一道、改口味、加减食材都行。'
  return (
    <div className={`composer${isFollowup ? ' is-followup' : ''}`}>
      {/* starter 档：提示语提到面板外上方（设计稿位置）。followup 档不在这里渲染。 */}
      {!isFollowup && <span className="ask-hint ask-hint-brief">{briefHint}</span>}

      {/* 面板层（2026-10-10）：设计稿把书写面画成一块**有底有边的暖陶面板**，
          输入区是面板里嵌的一块白面，按钮落在面板右下角、左下角留碗筷线稿位。
          上一版是"一叠稿纸"（上下发丝线 + 左边距线 + 格线），设计稿换的是整块
          面板语汇——底色/边框/圆角全在 styles/composer.css，这里只包一层结构。
          followup 档这层是**纯透传**（无面板样式），保持结果页书写面原样。 */}
      <div className="composer-panel">
        <label className="ask">
          {!isFollowup && (
            <span className="ask-label">
              {/* 标签前的小叶（2026-10-10 设计稿「🌿 你想吃什么」）：茎 + 两片叶，
                  currentColor 跟标签色走，不引入新颜色。aria-hidden 读屏不念。 */}
              <svg
                className="ask-label-leaf"
                viewBox="0 0 16 16"
                aria-hidden="true"
                focusable="false"
              >
                <g
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M8 14.5C8 10 6.6 6 3.6 3.2" />
                  <path d="M7.1 8.4C4.8 8 3.3 6.1 3.2 3.5 5.7 4 7.2 5.9 7.1 8.4Z" />
                  <path d="M9.1 10.6c2.4-.5 3.8-2.4 4-5-2.5.5-4 2.4-4 5Z" />
                </g>
              </svg>
              你想吃什么
            </span>
          )}
          {/* 稿纸语汇（上下发丝线 + 左侧边距线 + 横向格线）画在这一层及其伪元素上，
              textarea 本身一字符未改。结果页那一档**不再**关掉它——见 styles/base.css
              与 styles/composer.css 里两轮改写的理由。 */}
          <span className="ask-field">
            <textarea
              className="ask-input"
              ref={taRef}
              value={text}
              rows={isFollowup ? 1 : 2}
              disabled={running}
              placeholder={isFollowup ? '还想调整或换一道？直接说就行' : undefined}
              onChange={(e) => onText(e.target.value)}
              onKeyDown={(e) => {
                /* Enter = 开始（用户原话：「按一下 enter 键就应该要开始了，而不是换行」）。
                   判定逐条写在 data/enterToSend.ts：输入法选词为什么绝不能发送、
                   哪些修饰键按换行走。这里只接线。 */
                if (enterIntent(e.nativeEvent) !== 'send') return
                /* 这一段确实是"发送"意图：哪怕发不出去也要吃掉回车，别在正文里留空行。 */
                e.preventDefault()
                if (blocked || running || !text.trim()) return
                onSend()
              }}
            />
          </span>
        </label>

        <div className="ask-actions">
          {/* followup 档的提示语仍在动作行原位；starter 档它已在面板上方。 */}
          {isFollowup && <span className="ask-hint">{followHint}</span>}
          {/* 面板左下的碗筷（2026-10-11：改为**从设计稿切片**的位图）。
              此前的做法是在 JSX 里手绘 SVG 三部件（碗身/碗口/筷）再上色，
              无论怎么调色都到不了稿的质感——稿那枚碗有碗口高光、碗身渐变、
              筷子斜插与一整套柔影，手绘近似必然失真（用户 2026-10-11 判定
              "碗跟设计差很多"）。现在直接切稿的像素（bowl.png，
              scripts/assets/assets-build.cjs cut 产出，闸门 maskIoU 0.984），
              色值也一并来自稿，符合 check-layers "色字面量只进 tokens.css"。 */}
          {!isFollowup && <span className="ask-dish-art" aria-hidden="true" />}
          {/* 语音（2026-10-12）：在提交按钮**左边**——它是"另一种把话说进来"的方式，
              不是另一个主行动。不支持录音时 AskVoice 自己返回 null，这里不用判。 */}
          <AskVoice onTranscript={insert} />
          {/* 拍照认食材（2026-10-12）：排在语音**右边**——放左边会让语音那行隐私说明
              读成相机的说明（见 AskPhoto.tsx 文件头与 styles/composer.css 的取证）。
              它自己持文件 input 与上传状态，识别结果与语音走**同一条** insert 时序，
              所以"落到光标处、不自动发送"对两种输入是同一个实现。 */}
          <AskPhoto onRecognized={insert} />
          <button
            className="ask-go"
            type="button"
            disabled={blocked || !text.trim()}
            onClick={() => onSend()}
          >
            {/* 箭头只给首屏档（设计稿「开始 →」）；结果页「继续」与失败态「重试」
                不带——箭头是设计稿对这一处的形状要求，不是全局按钮语言。 */}
            {submitLabel ?? (isFollowup ? '继续' : '开始 →')}
          </button>
        </div>
      </div>
    </div>
  )
}


