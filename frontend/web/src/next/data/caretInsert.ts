import { useEffect, useRef } from 'react'
import { caretSpan, insertTranscript } from './voiceText.ts'

/**
 * 「把一段文字插到光标处」这一件事的复用点：书写面（AskComposer）持有 textarea，
 * 语音识别回来的是纯文本，两者之间只差这一个动作。
 *
 * 为什么不是三行内联在组件里：受控 textarea 的 value 由 React 写，
 * **同一帧里** `setSelectionRange` 会被随后那次写入冲掉（光标跳回末尾）——
 * 所以落位必须延到 DOM 更新之后。这一段时序细节放在组件里，下次谁改布局都会碰坏它。
 *
 * 只做插入，**不发送**：返回值里没有任何通往 onSend 的路径。
 */
export function useCaretInsert(text: string, onText: (next: string) => void) {
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const pendingCaret = useRef<number | null>(null)

  /* 无依赖数组 = 每次渲染后检查一次；没有待落位的光标就立刻返回（零成本）。 */
  useEffect(() => {
    const at = pendingCaret.current
    if (at === null) return
    pendingCaret.current = null
    const ta = taRef.current
    if (!ta) return
    ta.focus()
    ta.setSelectionRange(at, at)
  })

  const insert = (snippet: string) => {
    const ta = taRef.current
    const span = caretSpan(text, ta ? ta.selectionStart : null, ta ? ta.selectionEnd : null)
    const r = insertTranscript(text, span, snippet)
    if (r.next === text) return // 空结果：一个字都不改
    pendingCaret.current = r.caret
    onText(r.next)
  }

  return { taRef, insert }
}
