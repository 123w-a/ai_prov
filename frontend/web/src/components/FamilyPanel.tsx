import { useCallback, useEffect, useState } from 'react'
import type { FamilyData, FamilyMember } from '../types'
import type { MemberInput } from '../api/client'
import {
  addMember,
  deleteMember,
  fetchFamily,
  switchActiveMember,
  updateMember,
} from '../api/client'
import { Icon } from './Icon'

type EditingState =
  | { kind: 'closed' }
  | { kind: 'new' }
  | { kind: 'edit'; memberId: string }

interface Draft {
  name: string
  conditions: string
  allergens: string
  goal: string
  dislikes: string
}

const emptyDraft: Draft = { name: '', conditions: '', allergens: '', goal: '', dislikes: '' }

function splitTags(value: string): string[] {
  return value
    .split(/[，,、;；]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 24)
}

function draftFrom(member: FamilyMember): Draft {
  const { profile } = member
  return {
    name: member.name,
    conditions: profile.conditions.join('、'),
    allergens: profile.allergens.join('、'),
    goal: profile.goal ?? '',
    dislikes: profile.dislikes.join('、'),
  }
}

function inputFrom(draft: Draft): MemberInput {
  return {
    name: draft.name.trim(),
    profile: {
      conditions: splitTags(draft.conditions),
      allergens: splitTags(draft.allergens),
      goal: draft.goal.trim(),
      dislikes: splitTags(draft.dislikes),
    },
  }
}

function memberSummary(member: FamilyMember): string {
  const parts: string[] = []
  if (member.profile.conditions.length) parts.push(member.profile.conditions.join('、'))
  if (member.profile.allergens.length) parts.push(`忌 ${member.profile.allergens.join('、')}`)
  return parts.join(' · ') || '暂无健康约束'
}

export function FamilyPanel() {
  const [family, setFamily] = useState<FamilyData | null>(null)
  const [editing, setEditing] = useState<EditingState>({ kind: 'closed' })
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      setFamily(await fetchFamily())
    } catch {
      setNotice('家庭成员读取失败')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const activeMember = family?.members.find((m) => m.id === family.active_id)

  const startNew = () => {
    setDraft(emptyDraft)
    setEditing({ kind: 'new' })
  }

  const startEdit = (member: FamilyMember) => {
    setDraft(draftFrom(member))
    setEditing({ kind: 'edit', memberId: member.id })
  }

  const save = async () => {
    if (!draft.name.trim()) {
      setNotice('请填写成员称呼')
      return
    }
    setBusy(true)
    try {
      const input = inputFrom(draft)
      const next =
        editing.kind === 'edit'
          ? await updateMember(editing.memberId, input)
          : await addMember(input)
      setFamily(next)
      setEditing({ kind: 'closed' })
      setNotice('')
    } catch {
      setNotice('保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const switchTo = async (memberId: string) => {
    if (!family || family.active_id === memberId || busy) return
    setBusy(true)
    try {
      setFamily(await switchActiveMember(memberId))
      setNotice('')
    } catch {
      setNotice('切换失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (member: FamilyMember) => {
    if (busy) return
    setBusy(true)
    try {
      setFamily(await deleteMember(member.id))
      setNotice('')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '删除失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const editingMember =
    editing.kind === 'edit' ? family?.members.find((m) => m.id === editing.memberId) : undefined

  return (
    <section className="family-panel" aria-label="家庭成员画像">
      <header className="family-header">
        <strong>家庭成员</strong>
        <button
          type="button"
          className="icon-btn"
          aria-label="添加家庭成员"
          disabled={busy || editing.kind === 'new'}
          onClick={startNew}
        >
          <Icon name="plus" size={16} />
        </button>
      </header>

      {activeMember && editing.kind === 'closed' && (
        <p className="family-active">当前为「{activeMember.name}」推荐，下一条消息生效</p>
      )}

      {notice && <p className="family-notice">{notice}</p>}

      <ul className="family-members">
        {(family?.members ?? []).map((member) => (
          <li
            key={member.id}
            className={
              family && member.id === family.active_id ? 'family-member active' : 'family-member'
            }
          >
            <button
              type="button"
              className="family-member-main"
              disabled={busy || editing.kind !== 'closed'}
              onClick={() => void switchTo(member.id)}
              title="点击切换为当前推荐对象"
            >
              <strong>{member.name}</strong>
              <small>{memberSummary(member)}</small>
            </button>
            <span className="family-member-actions">
              <button
                type="button"
                className="icon-btn"
                aria-label={`编辑${member.name}`}
                disabled={busy}
                onClick={() => startEdit(member)}
              >
                <Icon name="book" size={14} />
              </button>
              <button
                type="button"
                className="icon-btn"
                aria-label={`删除${member.name}`}
                disabled={busy || (family?.members.length ?? 0) <= 1}
                onClick={() => void remove(member)}
              >
                <Icon name="close" size={14} />
              </button>
            </span>
          </li>
        ))}
      </ul>

      {editing.kind !== 'closed' && (
        <div className="family-editor">
          <strong>{editing.kind === 'new' ? '添加成员' : `编辑「${editingMember?.name ?? ''}」`}</strong>
          <label>
            称呼
            <input
              value={draft.name}
              maxLength={20}
              placeholder="如：妈妈"
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </label>
          <label>
            慢病情况（顿号分隔）
            <input
              value={draft.conditions}
              placeholder="如：高血压、糖尿病"
              onChange={(e) => setDraft({ ...draft, conditions: e.target.value })}
            />
          </label>
          <label>
            过敏原（硬约束，顿号分隔）
            <input
              value={draft.allergens}
              placeholder="如：虾、花生"
              onChange={(e) => setDraft({ ...draft, allergens: e.target.value })}
            />
          </label>
          <label>
            当前目标
            <input
              value={draft.goal}
              maxLength={40}
              placeholder="如：控盐减脂"
              onChange={(e) => setDraft({ ...draft, goal: e.target.value })}
            />
          </label>
          <label>
            不喜欢的食材（顿号分隔）
            <input
              value={draft.dislikes}
              placeholder="如：香菜、肥肉"
              onChange={(e) => setDraft({ ...draft, dislikes: e.target.value })}
            />
          </label>
          <div className="family-editor-actions">
            <button type="button" disabled={busy} onClick={() => void save()}>
              保存
            </button>
            <button type="button" disabled={busy} onClick={() => setEditing({ kind: 'closed' })}>
              取消
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
