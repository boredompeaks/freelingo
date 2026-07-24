import type {
  ElementType,
  HTMLAttributes,
  ReactNode,
  TouchEvent,
  MouseEvent,
} from 'react'
import { useState, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import { getTargetLanguageTextClass } from '@/lib/target-languages'
import { useAuthStore } from '@/store/auth'

interface TargetLanguageTextProps extends HTMLAttributes<HTMLElement> {
  languageCode?: string | null
  children: ReactNode
  as?: ElementType
  reading?: string | null
  translation?: string | null
}

export function TargetLanguageText({
  languageCode,
  children,
  as: Component = 'span',
  className,
  reading,
  translation,
  onContextMenu,
  onTouchStart,
  onTouchEnd,
  onTouchCancel,
  ...props
}: TargetLanguageTextProps) {
  const code = languageCode ?? ''
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const accessToken = useAuthStore((s) => s.accessToken)

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause()
        audioRef.current = null
      }
      if (longPressTimer.current) clearTimeout(longPressTimer.current)
    }
  }, [])

  const extractText = (node: ReactNode): string => {
    if (typeof node === 'string' || typeof node === 'number')
      return String(node)
    if (Array.isArray(node)) return node.map(extractText).join('')
    if (typeof node === 'object' && node !== null && 'props' in node) {
      return extractText((node as any).props.children)
    }
    return ''
  }

  const handlePlay = async () => {
    const text = extractText(children).trim()
    if (!text || loading || playing) return

    setLoading(true)
    try {
      const voice =
        typeof window !== 'undefined'
          ? (localStorage.getItem('tts_voice') ?? undefined)
          : undefined
      const res = await fetch('/api/tts', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({ text, voice }),
      })
      if (!res.ok) throw new Error('TTS error')

      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audioRef.current = audio

      audio.onended = () => {
        setPlaying(false)
        URL.revokeObjectURL(url)
      }
      audio.onerror = () => {
        setPlaying(false)
        setLoading(false)
        URL.revokeObjectURL(url)
      }

      setPlaying(true)
      setLoading(false)
      await audio.play()
    } catch {
      setLoading(false)
      setPlaying(false)
    }
  }

  const handleContextMenu = (e: MouseEvent<HTMLElement>) => {
    e.preventDefault()
    handlePlay()
    if (onContextMenu) onContextMenu(e)
  }

  const handleTouchStart = (e: TouchEvent<HTMLElement>) => {
    if (longPressTimer.current) clearTimeout(longPressTimer.current)
    longPressTimer.current = setTimeout(() => {
      handlePlay()
      if (window.navigator.vibrate) window.navigator.vibrate(50)
    }, 500)
    if (onTouchStart) onTouchStart(e)
  }

  const handleTouchEnd = (e: TouchEvent<HTMLElement>) => {
    if (longPressTimer.current) clearTimeout(longPressTimer.current)
    if (onTouchEnd) onTouchEnd(e)
  }

  const handleTouchCancel = (e: TouchEvent<HTMLElement>) => {
    if (longPressTimer.current) clearTimeout(longPressTimer.current)
    if (onTouchCancel) onTouchCancel(e)
  }

  return (
    <Component
      className={cn(
        getTargetLanguageTextClass(code),
        className,
        loading ? 'animate-pulse opacity-70' : '',
        playing ? 'text-fl-accent' : ''
      )}
      lang={code || undefined}
      onContextMenu={handleContextMenu}
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      onTouchCancel={handleTouchCancel}
      {...props}
    >
      {children}
      {(reading || translation) && (
        <span className="text-fl-fg mt-1 block font-mono text-xs leading-relaxed tracking-normal normal-case opacity-70">
          {[reading, translation].filter(Boolean).join(' · ')}
        </span>
      )}
    </Component>
  )
}
