/**
 * 语音输入的两段纯逻辑。
 *
 * 抽出来的理由不是"文件太长"：`insertTranscript` 决定**识别的字落在哪**，
 * 那是整条语音链路里唯一有语义判断的地方（追加还是覆盖、光标前后要不要补空格、
 * 空结果动不动草稿）。埋在组件的 onChange 里就只能靠手点验证，而这几条正是
 * 验收里点名的行为。这里不碰 React、不碰 navigator，`node --test` 能直接跑。
 */

export interface CaretSpan {
  start: number
  end: number
}

/**
 * 把 textarea 给的光标位置收进 `[0, text.length]` 且保证 `start <= end`。
 *
 * 必须容错：识别回来时用户已经把焦点移走（点过别处），此时 `selectionStart`
 * 仍是最后的位置，但值可能超界（草稿被别处改短过）。超界就截到合法区间，
 * 而不是抛错或落到字符串外——落错位置比不插入更糟，用户会看到字出现在别处。
 */
export function caretSpan(text: string, selStart: number | null, selEnd: number | null): CaretSpan {
  const len = text.length
  const clamp = (n: number | null, fallback: number) => {
    if (n === null || !Number.isFinite(n)) return fallback
    return Math.max(0, Math.min(len, Math.trunc(n)))
  }
  const start = clamp(selStart, len)
  const end = Math.max(start, clamp(selEnd, len))
  return { start, end }
}

/**
 * 把识别文本插进草稿。三条规则对应验收里「追加到光标处、不覆盖草稿」：
 *   · 有选区 → 替换选区（选区是用户自己划的，替换是标准编辑器语义）；
 *   · 无选区且光标前已有非空白字符 → 先补一个空格，否则
 *     「冰箱里只有鸡蛋和西兰花」+「今晚想吃鱼」会粘成一句读不通的话；
 *   · 光标后面还有字 → 不在识别文本后补空格（后面那句还没写完）。
 *
 * 空结果（识别回来是空白）返回原文与原地光标——**不动草稿**，
 * 这是验收单独点名的一条：不能因为"识别成功但没内容"就把已有草稿擦掉。
 */
export function insertTranscript(
  text: string,
  span: CaretSpan,
  snippet: string,
): { next: string; caret: number } {
  const clean = snippet.trim()
  if (clean.length === 0) return { next: text, caret: span.start }

  const before = text.slice(0, span.start)
  const after = text.slice(span.end)
  const needSpace = before.length > 0 && !/\s$/.test(before)
  const insert = needSpace ? ` ${clean}` : clean
  return { next: before + insert + after, caret: before.length + insert.length }
}

export type VoiceFailure = 'mic' | 'transcribe'

/** 空录音（MediaRecorder 一个字节都没给）与空识别各自的说明，都不动草稿。 */
export const EMPTY_AUDIO_NOTE = '没有录到声音，草稿没有改动。'
export const EMPTY_TRANSCRIPT_NOTE = '没有识别到文字，草稿没有改动。'
export const TRANSCRIBED_NOTE = '语音已转成文字，可以接着改，不会自动发送。'

/**
 * 失败原因如实成句。**权限被拒不是"不支持"**：按钮照旧可用（再点一次就是重试），
 * 界面只把浏览器给的原因写清楚——"不支持"是能力缺失，"被拒"是授权状态，
 * 两者混为一谈会让用户以为自己的浏览器没这个功能。
 */
export function voiceErrorMessage(err: unknown, kind: VoiceFailure): string {
  const name = err instanceof DOMException ? err.name : ''
  const raw = err instanceof Error ? err.message : String(err)

  if (kind === 'mic') {
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return '麦克风权限被拒绝，没有录到声音。再点一次可以重试，也可以先改浏览器地址栏里的权限设置。'
    }
    if (name === 'NotFoundError') return '这台设备上没有找到可用的麦克风。'
    if (name === 'NotReadableError') return '麦克风被别的程序占用了，没有录到声音。'
    return `无法使用麦克风：${raw}`
  }
  return `语音识别没有成功：${raw}`
}
