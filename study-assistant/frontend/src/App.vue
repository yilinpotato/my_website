<script setup>
import { computed, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useAuthStore } from './stores/auth'

const currentYear = new Date().getFullYear()
const authStore = useAuthStore()
const { isAuthenticated, user } = storeToRefs(authStore)

const role = computed(() => String(user.value?.role || ''))

const navItems = computed(() => {
  if (!isAuthenticated.value) return [{ to: '/', label: '首页' }]
  if (role.value === 'parent') {
    return [
      { to: '/', label: '首页' },
      { to: '/parent', label: '家长视图' },
    ]
  }
  return [
    { to: '/', label: '首页' },
    { to: '/note-assistant', label: '笔记' },
    { to: '/mind-map', label: '思维导图' },
    { to: '/error-book', label: '错题' },
    { to: '/dashboard', label: '分析' },
  ]
})

const handleLogout = () => {
  authStore.logout()
}

onMounted(async () => {
  if (authStore.token && !authStore.user) {
    try {
      await authStore.fetchProfile()
    } catch {
    }
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="site-header">
      <div class="brand">
        <span class="logo-dot" aria-hidden="true"></span>
        <div>
          <p class="brand-title">AI 学习助手</p>
          <p class="brand-subtitle">智能学习与个性化辅导</p>
        </div>
      </div>
      <nav class="site-nav">
        <RouterLink v-for="item in navItems" :key="item.to" :to="item.to">
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="auth-actions">
        <template v-if="isAuthenticated">
          <RouterLink class="ghost-button" to="/profile">{{ user?.display_name || '个人中心' }}</RouterLink>
          <button class="primary-button" type="button" @click="handleLogout">退出</button>
        </template>
        <template v-else>
          <RouterLink class="primary-button" to="/login">访客体验</RouterLink>
        </template>
      </div>
    </header>

    <main>
      <RouterView />
    </main>

    <footer class="site-footer">
      <p>Better notes · Deeper mastery · Happier learning</p>
      <small>© {{ currentYear }} AI Study Assistant</small>
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 2rem;
  padding: 1.5rem clamp(1.5rem, 4vw, 4rem) 2.5rem;
}

.site-header {
  border-radius: 2rem;
  padding: 1.5rem 2rem;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(255, 255, 255, 0.4);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1.5rem;
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.logo-dot {
  width: 2.8rem;
  height: 2.8rem;
  border-radius: 1.2rem;
  background: linear-gradient(135deg, #6f9dec, #8bd4c8);
  box-shadow: 0 10px 25px rgba(111, 157, 236, 0.35);
}

.brand-title {
  font-weight: 700;
  font-size: 1.2rem;
  color: #1b1b33;
}

.brand-subtitle {
  font-size: 0.95rem;
  color: rgba(27, 27, 51, 0.64);
}

.site-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  font-weight: 500;
}

.site-nav a {
  color: #1f355a;
  padding: 0.4rem 0.8rem;
  border-radius: 999px;
  transition: background 0.3s ease, color 0.3s ease;
}

.site-nav a.router-link-active,
.site-nav a:hover {
  background: rgba(111, 157, 236, 0.15);
  color: #0b2a6b;
}

.auth-actions {
  display: flex;
  gap: 0.6rem;
  margin-left: auto;
}

.ghost-button,
.primary-button {
  margin-left: 0;
  padding: 0.75rem 1.6rem;
  border-radius: 999px;
  border: 1px solid rgba(17, 62, 125, 0.3);
  color: #0c2b53;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 0 0 rgba(0, 0, 0, 0);
  transition: all 0.3s ease;
}
.ghost-button:hover,
.primary-button:hover {
  border-color: rgba(111, 157, 236, 0.8);
  box-shadow: 0 8px 20px rgba(13, 52, 104, 0.15);
}

.primary-button {
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
  border: none;
}

main {
  flex: 1;
}

.site-footer {
  text-align: center;
  color: rgba(10, 20, 40, 0.7);
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

@media (max-width: 768px) {
  .site-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .auth-actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
