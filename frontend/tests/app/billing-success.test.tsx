import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import React from 'react'

const { mockApiFetch, mockRefreshToken, mockPush } = vi.hoisted(() => ({
  mockApiFetch: vi.fn(),
  mockRefreshToken: vi.fn(),
  mockPush: vi.fn(),
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, vars?: Record<string, unknown>) =>
    vars?.seconds ? `${key}:${vars.seconds}` : key,
}))

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}))

vi.mock('next/link', () => ({
  default: ({
    href,
    children,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement>) =>
    React.createElement('a', { href: String(href), ...props }, children),
}))

vi.mock('@/lib/api', () => ({
  apiFetch: mockApiFetch,
  refreshToken: mockRefreshToken,
}))


import BillingSuccessPage from '@/app/(auth)/billing/success/page'
import { useAuthStore } from '@/store/auth'

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function me(subscriptionStatus: string) {
  return {
    id: 1,
    username: 'learner',
    display_name: 'Learner',
    email: 'learner@example.com',
    native_language: 'es',
    ui_locale: 'en',
    role: 'user',
    conversation_max_duration: 30,
    conversation_inactivity_timeout: 10,
    subscription_status: subscriptionStatus,
    subscription_ends_at: null,
    trial_used: false,
  }
}

beforeEach(() => {
  mockApiFetch.mockReset()
  mockPush.mockReset()
  useAuthStore.setState({ accessToken: 'token', user: null })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('BillingSuccessPage', () => {
  it('shows confirmed Premium copy only after /me returns an active subscription', async () => {
    mockApiFetch.mockResolvedValueOnce(jsonResponse(me('active')))

    render(<BillingSuccessPage />)

    expect(screen.getByText('successCheckingTitle')).toBeDefined()
    expect(screen.queryByText('successTitle')).toBeNull()

    await waitFor(() => expect(screen.getByText('successTitle')).toBeDefined())
    expect(screen.getByText('successDesc')).toBeDefined()
    expect(screen.getByText('successRedirect:5')).toBeDefined()
    expect(useAuthStore.getState().user?.subscription_status).toBe('active')
  })

  it('refreshes the session before checking /me when the access token is missing', async () => {
    useAuthStore.setState({ accessToken: null, user: null })
    mockRefreshToken.mockImplementationOnce(async () => {
      useAuthStore.getState().setTokens('new-token')
      return 'new-token'
    })
    mockApiFetch.mockResolvedValueOnce(jsonResponse(me('trialing')))

    render(<BillingSuccessPage />)

    await waitFor(() => expect(screen.getByText('successTitle')).toBeDefined())
    expect(mockRefreshToken).toHaveBeenCalled()
    expect(useAuthStore.getState().accessToken).toBe('new-token')
    expect(useAuthStore.getState().user?.subscription_status).toBe('trialing')
  })

  it('does not show active Premium copy when /me never confirms the subscription', async () => {
    vi.useFakeTimers()
    mockApiFetch.mockImplementation(() => Promise.resolve(jsonResponse(me('none'))))

    render(<BillingSuccessPage />)

    expect(screen.getByText('successCheckingTitle')).toBeDefined()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(6100)
    })

    expect(screen.getByText('successPendingTitle')).toBeDefined()
    expect(screen.queryByText('successTitle')).toBeNull()
    expect(screen.queryByText('successRedirect:5')).toBeNull()
  })
})
