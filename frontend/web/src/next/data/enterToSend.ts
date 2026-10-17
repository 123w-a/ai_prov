/**
 * 输入框里「哪一次回车算发送」的唯一判定。
 *
 * 抽出来的理由和 AskComposer 本身一样：按钮与回车必须是**同一条**提交路径，
 * 而"这一下回车到底是什么意思"是这条路径上唯一需要逐条论证的地方——留在 JSX 里
 * 就只能靠肉眼维持，抽到这里才能被单测逐一钉住（enterToSend.test.ts）。
 *
 * 三种意图，穷尽所有回车：
 *   send     不带修饰键的回车：用户按下的就是"开始"。
 *   newline  带 Shift/Alt/Ctrl/Meta 的回车：多行输入与各家快捷键，不抢。
 *   ignore   ①不是回车；②输入法正在拼字（见下）。
 *
 * 输入法这一条是**功能正确性**，不是优化：中文/日文拼字过程中按回车是"选词确认"，
 * 此时发送等于把用户刚拼到一半的候选词当成定稿替他发出去。两个判据都要留着——
 * isComposing 是标准判据；keyCode 229 是部分 IME（以及旧 WebKit）在
 * compositionupdate 期间唯一给得出的信号，只判前者会在这些环境里漏掉。
 *
 * 命中 ignore 的调用方**不得 preventDefault**：那一下回车属于输入法，不属于我们。
 */
export type EnterIntent = 'send' | 'newline' | 'ignore'

/** 只取判定用到的字段，不依赖 React 类型——单测可以直接喂字面量对象。 */
export interface EnterKeyLike {
  key: string
  isComposing: boolean
  keyCode: number
  shiftKey: boolean
  altKey: boolean
  ctrlKey: boolean
  metaKey: boolean
}

export function enterIntent(e: EnterKeyLike): EnterIntent {
  if (e.key !== 'Enter') return 'ignore'
  if (e.isComposing || e.keyCode === 229) return 'ignore'
  if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return 'newline'
  return 'send'
}
