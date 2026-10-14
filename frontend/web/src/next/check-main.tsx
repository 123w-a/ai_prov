import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './next.css'
import NextApp from './NextApp.tsx'

// 接通检查页（/check.html）：不画界面，只把真实返回的字段与时序摊开看。
// 它的历史价值见 docs/08-真实接口实测与原型推翻.md —— 靠它才发现整轮 354.7s、106s 沉默、阶段会重复。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <NextApp />
  </StrictMode>,
)
