import './BookcometLogo.css'

const LOGO_SRC = '/bookcomet-logo.png'
const INTRINSIC_WIDTH = 1024
const INTRINSIC_HEIGHT = 768

export type BookcometLogoVariant = 'workspace' | 'auth' | 'footer' | 'picker'

type Props = {
  variant: BookcometLogoVariant
  className?: string
  alt?: string
}

export function BookcometLogo({ variant, className = '', alt = '' }: Props) {
  return (
    <img
      className={['bookcomet-logo', `bookcomet-logo--${variant}`, className].filter(Boolean).join(' ')}
      src={LOGO_SRC}
      alt={alt}
      width={INTRINSIC_WIDTH}
      height={INTRINSIC_HEIGHT}
      decoding="async"
    />
  )
}
