import { useVoiceInput } from '../data/voiceInput.ts'

/**
 * 书写面里的语音按钮（2026-10-12）。
 *
 * 位置由 AskComposer 决定（`.ask-actions` 内、`ask-go` 左侧）；形状按设计稿那一行的
 * 语汇来：**细描边圆形、无填充、currentColor**，图标是标准麦克风（圆柱 + 底弧 + 短柄），
 * 与同文件里那片叶 `svg` 同笔宽 1.4——同一行里两枚图形不该是两种线重。
 *
 * 三条降级（每一条都有界面表现，不静默）：
 *   · 非安全页面 / 浏览器不支持录音 → 按钮仍显示，旁边直接说明不可用原因；
 *   · 麦克风权限被拒 / 没有设备 / 被占用 → 按钮**仍可用**（再点一次即重试），
 *     原因写在按钮旁边的浮层里，按钮本身不变灰；
 *   · 后端 available:false 或转写失败 → 后端原话如实带出（voiceErrorMessage），草稿不动。
 *
 * 「识别的字只填回输入框、绝不自动发送」不在本组件里实现：它把纯文本交给
 * AskComposer 的插入回调，本组件**没有**任何通往 onSend 的路径——这是结构上的保证，
 * 不是"我小心没写"。
 */
export function AskVoice({ onTranscript }: { onTranscript: (text: string) => void }) {
  const { supported, supportNotice, state, notice, toggle } = useVoiceInput(onTranscript)

  const recording = state === 'recording'
  const transcribing = state === 'transcribing'
  const label = recording
    ? '结束录音'
    : transcribing
      ? '正在识别语音'
      : supported
        ? '用语音说话'
        : '查看麦克风不可用原因'

  return (
    <span className="ask-voice">
      {/* 隐私边界写在这里：音频走后端，**而且后端会再送云端**（services/speech.py
          先 upload_to_oss 再交阿里云 DashScope 转写），所以只说"上传到本机后端"
          会让人以为音频不出本机——那是不完整的表述，按诚实底线必须把云端这一步写明。 */}
      <span className="ask-voice-priv">
        {supportNotice ?? '语音会上传到本机后端，再送云端识别'}
      </span>

      <button
        className={`ask-voice-btn${recording ? ' is-rec' : ''}${transcribing ? ' is-busy' : ''}${!supported ? ' is-unavailable' : ''}`}
        type="button"
        aria-label={label}
        title={supportNotice ?? label}
        aria-pressed={recording}
        disabled={transcribing}
        onClick={toggle}
      >
        <svg className="ask-voice-ico" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <g
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="6" y="1.6" width="4" height="7.6" rx="2" />
            <path d="M3.5 7.4a4.5 4.5 0 0 0 9 0" />
            <path d="M8 11.9v2.5" />
          </g>
        </svg>
      </button>

      {/* 可见的录音状态：只有图标变色不算"看得见"，所以另起一行字。 */}
      {recording && (
        <span className="ask-voice-live" role="status">
          正在录音，点一下结束
        </span>
      )}
      {transcribing && (
        <span className="ask-voice-live is-busy" role="status">
          正在识别…
        </span>
      )}

      {/* 失败与空结果的说明：浮层，不参与布局（出现时不推挤输入框与按钮）。 */}
      {notice && (
        <span className="ask-voice-note" role="status">
          {notice}
        </span>
      )}
    </span>
  )
}
