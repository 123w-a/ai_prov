/**
 * 编号卡片的**卡头**（C 设计稿每个卡片左上角那个「1.」「2.」编号 + 标题）。
 *
 * 单独成文件是因为周报页现在有 6 张编号卡，分住在四个组件里；卡头要一模一样，
 * 复制六遍必然长得不一样（先是间距差 2px，然后是有人加了 note 有人没加）。
 * 放 `blocks/` 而不是 `ui/`：它带着本期版面的业务措辞与编号约定，
 * 不是通用原语——`ui/` 只放 SparkLine / RingProgress / RateChart / Icon 这类
 * 谁都能用、且与本期内容无关的东西。
 */
export function CardHead({ n, title, note }: { n: string; title: string; note?: string }) {
  return (
    <p className="wk-card-k">
      <i className="wk-card-n">{n}</i>
      <span className="wk-card-t">{title}</span>
      {note ? <em className="wk-card-note">{note}</em> : null}
    </p>
  )
}
