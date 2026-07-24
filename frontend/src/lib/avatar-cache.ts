import { useAuthStore } from '@/store/auth'
import { refreshToken } from '@/lib/api'

type Subscriber = (src: string | null) => void

let cacheKey: string | null = null
let objectUrl: string | null = null
let pending: Promise<string | null> | null = null
const subscribers = new Set<Subscriber>()

function notify() {
  for (const subscriber of subscribers) {
    subscriber(objectUrl)
  }
}

function resetObjectUrl() {
  if (objectUrl?.startsWith('blob:')) URL.revokeObjectURL(objectUrl)
  objectUrl = null
}

export function subscribeAvatar(subscriber: Subscriber) {
  subscribers.add(subscriber)
  subscriber(objectUrl)
  return () => {
    subscribers.delete(subscriber)
  }
}

export function clearAvatarCache() {
  cacheKey = null
  pending = null
  resetObjectUrl()
  notify()
}

export function loadAvatar(avatar: string, accessToken: string | null) {
  const key = `${avatar}:${accessToken ?? ''}`
  if (cacheKey === key && objectUrl) return Promise.resolve(objectUrl)
  if (cacheKey === key && pending) return pending

  cacheKey = key
  resetObjectUrl()
  notify()

  if (avatar.startsWith('data:image/')) {
    objectUrl = avatar
    notify()
    return Promise.resolve(objectUrl)
  }

  const fetchAvatar = (token: string | null) =>
    fetch('/api/auth/me/avatar-file', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: 'include',
    })

  pending = fetchAvatar(accessToken)
    .then(async (res) => {
      if (res.status === 401 && accessToken) {
        const newToken = await refreshToken()
        if (newToken) {
          res = await fetchAvatar(newToken)
        }
      }
      if (!res.ok) return null
      const blob = await res.blob()
      if (cacheKey !== key) return null
      objectUrl = URL.createObjectURL(blob)
      notify()
      return objectUrl
    })
    .catch(() => null)
    .finally(() => {
      pending = null
    })

  return pending
}
