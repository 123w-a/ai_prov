import type { SVGProps } from 'react'

export type IconName =
  | 'book'
  | 'check'
  | 'chef'
  | 'chat'
  | 'close'
  | 'concierge'
  | 'image'
  | 'leaf'
  | 'menu'
  | 'mic'
  | 'plus'
  | 'search'
  | 'send'
  | 'shield'
  | 'spark'
  | 'trash'
  | 'utensils'
  | 'warning'

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 20, ...props }: IconProps) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
    focusable: false,
    ...props,
  }

  switch (name) {
    case 'book':
      return (
        <svg {...common}>
          <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5z" />
          <path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z" />
        </svg>
      )
    case 'check':
      return (
        <svg {...common}>
          <path d="m5 12 4 4L19 6" />
        </svg>
      )
    case 'chef':
      return (
        <svg {...common}>
          <path d="M7 10a4 4 0 0 1 1-7.87A4.5 4.5 0 0 1 16 3a4 4 0 0 1 1 7" />
          <path d="M6 10h12l-1 10H7z" />
          <path d="M9 15h6" />
        </svg>
      )
    case 'chat':
      return (
        <svg {...common}>
          <path d="M20 15a4 4 0 0 1-4 4H8l-5 3 1.6-4.8A7 7 0 0 1 3 13V8a4 4 0 0 1 4-4h9a4 4 0 0 1 4 4z" />
        </svg>
      )
    case 'close':
      return (
        <svg {...common}>
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      )
    case 'concierge':
      return (
        <svg {...common}>
          <path d="M4 17h16M6 17a6 6 0 0 1 12 0M12 7v2M10 5h4" />
          <path d="M3 20h18" />
        </svg>
      )
    case 'image':
      return (
        <svg {...common}>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <circle cx="8.5" cy="9" r="1.5" />
          <path d="m4 17 4.5-4 3 2.5 3.5-4 5 5.5" />
        </svg>
      )
    case 'leaf':
      return (
        <svg {...common}>
          <path d="M20 4C11 4 5 8 5 14a5 5 0 0 0 5 5c6 0 10-6 10-15Z" />
          <path d="M4 21c2-6 6-9 12-12" />
        </svg>
      )
    case 'menu':
      return (
        <svg {...common}>
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      )
    case 'mic':
      return (
        <svg {...common}>
          <rect x="9" y="3" width="6" height="11" rx="3" />
          <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6" />
        </svg>
      )
    case 'plus':
      return (
        <svg {...common}>
          <path d="M12 5v14M5 12h14" />
        </svg>
      )
    case 'search':
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="7" />
          <path d="m16 16 5 5" />
        </svg>
      )
    case 'send':
      return (
        <svg {...common}>
          <path d="m21 3-7 18-4-7-7-4zM10 14 21 3" />
        </svg>
      )
    case 'shield':
      return (
        <svg {...common}>
          <path d="M12 3 20 6v5c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6z" />
          <path d="m8.5 12 2.2 2.2 4.8-5" />
        </svg>
      )
    case 'spark':
      return (
        <svg {...common}>
          <path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z" />
          <path d="m19 15 .8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" />
        </svg>
      )
    case 'trash':
      return (
        <svg {...common}>
          <path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" />
        </svg>
      )
    case 'utensils':
      return (
        <svg {...common}>
          <path d="M7 3v7M4 3v4a3 3 0 0 0 6 0V3M7 10v11M16 3v18M16 3c3 1 4 4 4 7h-4" />
        </svg>
      )
    case 'warning':
      return (
        <svg {...common}>
          <path d="M12 4 2.8 20h18.4z" />
          <path d="M12 9v5M12 17.5v.5" />
        </svg>
      )
  }
}
