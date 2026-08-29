import { useEffect, useState } from 'react'
import { fetchFavorites, starMessage } from '../api/client'
import type { FavoriteItem } from '../types'
import { Icon } from './Icon'

export function FavoritesPanel({ onOpenSession }: { onOpenSession: (sid: string) => void }) {
  const [items, setItems] = useState<FavoriteItem[] | null>(null)

  const load = () =>
    fetchFavorites()
      .then(setItems)
      .catch(() => setItems([]))

  useEffect(() => {
    void load()
  }, [])

  const remove = (sid: string, recId: number) => {
    setItems((list) => (list ?? []).filter((i) => !(i.sid === sid && i.rec_id === recId)))
    void starMessage(sid, recId, false).then(load).catch(load)
  }

  return (
    <div className="favorites-panel">
      <div className="favorites-header">
        <div>
          <h2>我的收藏</h2>
          <p>点过 ★ 的回答都会留在这里，跨会话随时回看。</p>
        </div>
        <button type="button" className="favorites-refresh" onClick={() => void load()}>
          刷新
        </button>
      </div>

      {items === null ? (
        <p className="favorites-empty">正在加载收藏…</p>
      ) : items.length === 0 ? (
        <p className="favorites-empty">还没有收藏。聊到满意的菜，点回答旁的 ★ 留下来。</p>
      ) : (
        <div className="favorites-grid">
          {items.map((item) => (
            <article key={`${item.sid}-${item.rec_id}`} className="favorite-card">
              {item.image_url && (
                <img src={item.image_url} alt={item.dish} loading="lazy" />
              )}
              <div className="favorite-body">
                <strong>{item.dish}</strong>
                <small>来自会话：{item.session_title || item.user_text}</small>
              </div>
              <div className="favorite-actions">
                <button type="button" onClick={() => onOpenSession(item.sid)}>
                  去原会话
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => remove(item.sid, item.rec_id)}
                >
                  取消收藏
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
      <p className="favorites-note">
        <Icon name="star" size={11} /> 收藏保存在本机，与对话数据同库
      </p>
    </div>
  )
}
