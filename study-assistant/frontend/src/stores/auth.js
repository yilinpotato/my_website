import { defineStore } from 'pinia'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const handleResponse = async (response) => {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = data?.message || '请求失败，请稍后再试'
    const error = new Error(message)
    error.status = response.status
    throw error
  }
  return data
}

const apiFetch = async (path, options = {}) => {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, options)
    return await handleResponse(response)
  } catch (error) {
    if (error.name === 'TypeError' || error.message === 'Failed to fetch') {
      throw new Error('无法连接后端服务，请确认 API 已启动并允许访问')
    }
    throw error
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('ai-study-token') || '',
    user: null,
    loading: false,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
  },
  actions: {
    setToken(token) {
      this.token = token
      if (token) {
        localStorage.setItem('ai-study-token', token)
      } else {
        localStorage.removeItem('ai-study-token')
      }
    },
    async login({ email, password }) {
      this.loading = true
      try {
        const data = await apiFetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        })
        this.setToken(data.token)
        await this.fetchProfile()
        return data
      } finally {
        this.loading = false
      }
    },
    async guestLogin() {
      const data = await apiFetch('/api/auth/guest', { method: 'POST' })
      this.setToken(data.token)
      await this.fetchProfile()
    },
    async register(payload) {
      return apiFetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    },
    async verifyEmail(payload) {
      const data = await apiFetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (data.token) {
        this.setToken(data.token)
        await this.fetchProfile()
      }
      return data
    },
    async requestPasswordReset(email) {
      return apiFetch('/api/auth/request-password-reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
    },
    async resetPassword(payload) {
      return apiFetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
    },
    async fetchProfile() {
      if (!this.token) return null
      try {
        const data = await apiFetch('/api/profile', {
          headers: { Authorization: `Bearer ${this.token}` },
        })
        this.user = data
        return data
      } catch (error) {
        if (error?.status === 401) {
          this.logout()
          throw new Error('登录已过期，请重新登录')
        }
        throw error
      }
    },
    async updateProfile(updates) {
      try {
        const data = await apiFetch('/api/profile', {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${this.token}`,
          },
          body: JSON.stringify(updates),
        })
        this.user = data
        return data
      } catch (error) {
        if (error?.status === 401) {
          this.logout()
          throw new Error('登录已过期，请重新登录')
        }
        throw error
      }
    },
    async changePassword(payload) {
      try {
        return await apiFetch('/api/profile/password', {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${this.token}`,
          },
          body: JSON.stringify(payload),
        })
      } catch (error) {
        if (error?.status === 401) {
          this.logout()
          throw new Error('登录已过期，请重新登录')
        }
        throw error
      }
    },
    logout() {
      this.user = null
      this.setToken('')
    },
  },
})
