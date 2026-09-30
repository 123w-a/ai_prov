import { rangeLabel, trendLabel, useWeeklyReport } from '../data/firstScreen.ts'

/**
 * 周报房间（2026-09-29 户型图第 3 步 · 落地顺序第 3 间）。
 *
 * 与右栏 WeekBlock **同源不同版**：同一份 /api/reports/weekly，右栏是 278px 摘要，
 * 这里是整页。房间页多放两样右栏放不下的——后端给的可执行建议、反馈计数。
 *
 * 三条验收判据（户型图第四节）逐条对应：
 *   1 真实数据可展示 → 灯 / 常做的菜 / 趋势 / 护栏 / 建议 / 反馈，全部来自后端；
 *   2 不足数据有明确空态 → has_data=false 显示后端 message，趋势照实写
 *     「数据不足」，**不画趋势线**（画了就是替后端编结论）；
 *   3 能从某顿回到今晚 → 后端 top_dishes 只给 [菜名, 次数]，**不给逐餐记录**
 *     （/api/reports/weekly 无 meals 列表字段，前端不越界造接口），所以这一步
 *     落在「点常做的菜 → 回今晚并接上话头」。要做成真·逐餐，得后端加接口。
 *
 * 点菜走自定义事件而不是 props 回调：今晚房在 Shell 里是**常驻挂载**（只藏不卸，
 * 保住跑一半的那一轮），所以它的监听一直活着；回调则要 Shell 把 handler 穿两层。
 */
export default function WeeklyView() {
  const { view, loading, error } = useWeeklyReport()

  if (loading) {
    return (
      <section className="wk" data-room="weekly">
        <p className="wk-loading">正在读取本周…</p>
      </section>
    )
  }

  if (error) {
    // 失败要说话：退回 null 装作「这周没数据」，会把故障写成用户没吃饭。
    return (
      <section className="wk" data-room="weekly">
        <h2 className="wk-h">周报</h2>
        <p className="wk-err">本周报告读取失败：{error}</p>
      </section>
    )
  }

  if (!view) return null

  if (view.isEmpty) {
    return (
      <section className="wk" data-room="weekly">
        <h2 className="wk-h">周报</h2>
        <p className="wk-empty">{view.message}</p>
      </section>
    )
  }

  const pick = (name: string) => {
    window.dispatchEvent(new CustomEvent('dish-pick', { detail: name }))
    window.location.hash = 'room=tonight'
  }

  const fbTags = view.feedback ? Object.entries(view.feedback.tags) : []

  return (
    <section className="wk" data-room="weekly">
      <h2 className="wk-h">周报</h2>
      <p className="wk-sub">
        {rangeLabel(view.range) ?? '本周'} · {view.meals} 餐
      </p>

      {view.lights.length > 0 && (
        <div className="wk-sec">
          <p className="wk-k">营养灯</p>
          <ul className="wk-lights">
            {view.lights.map((l) => (
              <li
                key={l.nutrient}
                className="wk-light"
                data-level={l.red > 0 ? 'red' : l.yellow > 0 ? 'yellow' : 'green'}
              >
                {/* 9px 实心点：只做状态点，语义色不上卡片底/按钮底（lave 硬边界）。 */}
                <span className="wk-dot" aria-hidden="true" />
                <span className="wk-lname">{l.nutrient}</span>
                <span className="wk-lstat">
                  {l.green > 0 && <>绿 {l.green}</>}
                  {l.yellow > 0 && <> · 黄 {l.yellow}</>}
                  {l.red > 0 && <> · 红 {l.red}</>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {view.dishes.length > 0 && (
        <div className="wk-sec">
          <p className="wk-k">常做的菜 · 点一道回今晚</p>
          <ul className="wk-dishes">
            {view.dishes.map(([name, n]) => (
              <li key={name}>
                <button className="wk-dish" type="button" onClick={() => pick(name)}>
                  <span className="wk-dish-name">{name}</span>
                  <b className="wk-dish-n">{n}</b>
                  <span className="wk-dish-go">再做一顿 →</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {view.trends.length > 0 && (
        <div className="wk-sec">
          <p className="wk-k">趋势</p>
          <p className="wk-note">
            {view.trends.map((t) => `${t.label} ${trendLabel(t.state)}`).join('、')}
            ——数据不足以判断时不画趋势线，画出来的线是编的。
          </p>
        </div>
      )}

      {view.recommendations.length > 0 && (
        <div className="wk-sec">
          <p className="wk-k">建议</p>
          <ul className="wk-list">
            {view.recommendations.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="wk-sec">
        <p className="wk-k">护栏</p>
        <p className="wk-note">
          {view.guardrail > 0 ? `本周触发 ${view.guardrail} 次` : '本周没有触发'}
        </p>
      </div>

      <div className="wk-sec">
        <p className="wk-k">反馈{view.feedback ? ` · 收到 ${view.feedback.count} 条` : ''}</p>
        {fbTags.length > 0 ? (
          <ul className="wk-tags">
            {fbTags.map(([tag, n]) => (
              <li key={tag}>
                {tag}
                <b>{n}</b>
              </li>
            ))}
          </ul>
        ) : (
          <p className="wk-note">还没有收到反馈</p>
        )}
      </div>
    </section>
  )
}
