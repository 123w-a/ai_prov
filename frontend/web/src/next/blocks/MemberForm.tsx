import { useState } from 'react'
import type { MemberInput } from '../../api/client.ts'
import { MEMBER_FIELDS, toDraft, toMemberInput, type MemberDraft } from '../data/memberDraft.ts'
import { enterIntent } from '../data/enterToSend.ts'

/**
 * 成员编辑器（新增 / 修改共用一份）。
 *
 * 转换逻辑（草稿 ↔ MemberInput、字段表）在 data/memberDraft.ts——那里解释了
 * 为什么必须覆盖 profile 的全部字段：后端 PUT 是**整成员覆盖**，漏掉谁就清空谁。
 *
 * 字段表驱动而不是 12 个 useState：blocks 层单文件有 150 行上限，而且"哪些字段存在"
 * 只该写一遍——散在两处必然漏一个。
 *
 * 列表类字段用顿号、逗号或空格分隔输入：后端收 string[]，为它做"加一项"控件会
 * 显著变重，而这几项通常只有一到三个值。
 */
export function MemberForm({
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  /** null = 新增成员；否则是"把这个人改成"。 */
  initial: MemberInput | null
  busy: boolean
  onSubmit: (input: MemberInput) => void
  onCancel: () => void
}) {
  const [d, setD] = useState<MemberDraft>(() => toDraft(initial))
  const set = (k: keyof MemberDraft, v: string) => setD((s) => ({ ...s, [k]: v }))

  const nameOk = d.name.trim() !== ''
  const canSave = nameOk && !busy

  return (
    <form
      className="mf"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSave) onSubmit(toMemberInput(d))
      }}
      /* Enter = 保存（用户原话：「按一下 enter 键就应该要开始了」）。
         判定复用 data/enterToSend.ts，与今晚房的输入框同一份：'send' 才拦下来、
         'newline'/'ignore' 一律放行。拦下后走 requestSubmit——它是本表单**现成的**
         那条 onSubmit，不在这里另写一套提交逻辑（disabled 状态也照旧由 canSave 管）。
         只认 <input>：<select>（性别）的回车是打开下拉，不能吃掉。 */
      onKeyDown={(e) => {
        if (!(e.target instanceof HTMLInputElement)) return
        if (enterIntent(e.nativeEvent) !== 'send') return
        e.preventDefault()
        if (!canSave) return
        e.currentTarget.requestSubmit()
      }}
    >
      <div className="mf-grid">
        {MEMBER_FIELDS.map((f) => (
          <label key={f.k} className={`mf-field${f.narrow ? ' mf-num' : ''}`}>
            <span className="mf-label">{f.label}</span>
            {f.k === 'sex' ? (
              <select className="mf-input" value={d.sex} onChange={(e) => set('sex', e.target.value)}>
                <option value="">未填</option>
                <option value="female">女</option>
                <option value="male">男</option>
                <option value="other">其他</option>
              </select>
            ) : (
              <input
                className="mf-input"
                value={d[f.k]}
                placeholder={f.ph}
                inputMode={f.narrow ? 'decimal' : undefined}
                autoFocus={f.k === 'name'}
                onChange={(e) => set(f.k, e.target.value)}
              />
            )}
          </label>
        ))}
      </div>

      <div className="mf-actions">
        <button type="submit" className="mf-save" disabled={!canSave}>
          {busy ? '保存中…' : '保存'}
        </button>
        <button type="button" className="mf-cancel" onClick={onCancel} disabled={busy}>
          取消
        </button>
        {!nameOk && <span className="mf-hint">姓名不能为空</span>}
      </div>
    </form>
  )
}
