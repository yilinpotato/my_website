<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const auth = useAuthStore()
const router = useRouter()

const emailToBind = ref('')
const nicknameToBind = ref('')
const message = ref('')
const error = ref('')

const children = ref([])
const loadingChildren = ref(false)

const isParent = computed(() => (auth.user?.role || '') === 'parent')

const reportLoading = ref(false)
const reportError = ref('')
const weeklyReport = ref({
  week: '',
  overallTone: '',
  aiSummary: '',
  encouragement: '',
})
const weakTopics = ref([])
const highlightCards = ref([])

const ensureAuth = async () => {
  if (!auth.token) {
    router.push('/login')
    return false
  }
  try {
    await auth.fetchProfile()
    return true
  } catch {
    router.push('/login')
    return false
  }
}

const authedFetchJson = async (path, options = {}) => {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${auth.token}`,
    },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    if (res.status === 401) {
      auth.logout()
      router.push('/login')
      throw new Error('登录已过期，请重新登录')
    }
    throw new Error(data?.message || '请求失败')
  }
  return data
}

const loadChildren = async () => {
  if (!(await ensureAuth())) return
  if (!isParent.value) return
  loadingChildren.value = true
  try {
    const data = await authedFetchJson('/api/parent/children')
    children.value = Array.isArray(data?.items) ? data.items : []
  } finally {
    loadingChildren.value = false
  }
}

const loadParentReport = async () => {
  if (!(await ensureAuth())) return
  if (!isParent.value) return

  reportError.value = ''
  reportLoading.value = true
  try {
    const data = await authedFetchJson('/api/parent/report')
    weeklyReport.value = {
      week: data?.week || '',
      overallTone: data?.overallTone || '',
      aiSummary: data?.aiSummary || '',
      encouragement: data?.encouragement || '',
    }
    weakTopics.value = Array.isArray(data?.weakTopics) ? data.weakTopics : []
    highlightCards.value = Array.isArray(data?.highlightCards) ? data.highlightCards : []
  } catch (e) {
    reportError.value = e?.message || '获取周报失败'
    weeklyReport.value = { week: '', overallTone: '', aiSummary: '', encouragement: '' }
    weakTopics.value = []
    highlightCards.value = []
  } finally {
    reportLoading.value = false
  }
}

onMounted(async () => {
  await loadChildren()
  await loadParentReport()
})

const bindByEmail = async () => {
  message.value = ''
  error.value = ''
  if (!(await ensureAuth())) return
  if (!isParent.value) {
    error.value = '当前账号不是家长角色'
    return
  }
  if (!emailToBind.value.trim()) {
    error.value = '请输入学生邮箱'
    return
  }
  try {
    // 复用 profile PUT 的邮箱绑定逻辑（立即绑定，不需要学生确认）
    await auth.updateProfile({ linked_email: emailToBind.value.trim() })
    message.value = '已通过邮箱绑定学生'
    emailToBind.value = ''
    await loadChildren()
  } catch (e) {
    error.value = e?.message || '绑定失败'
  }
}

const requestByNickname = async () => {
  message.value = ''
  error.value = ''
  if (!(await ensureAuth())) return
  if (!isParent.value) {
    error.value = '当前账号不是家长角色'
    return
  }
  if (!nicknameToBind.value.trim()) {
    error.value = '请输入学生昵称（或用户名）'
    return
  }
  try {
    await authedFetchJson('/api/bind/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method: 'nickname', value: nicknameToBind.value.trim() }),
    })
    message.value = '已发送绑定请求，等待学生同意'
    nicknameToBind.value = ''
  } catch (e) {
    error.value = e?.message || '发送失败'
  }
}
</script>

<template>
  <section v-if="!auth.token" class="hero">
    <p class="eyebrow">Parent View</p>
    <h1>家长视图</h1>
    <p class="lead">请先登录家长账号后使用。</p>
    <div class="hero-actions">
      <RouterLink class="ghost link-button" to="/">返回首页</RouterLink>
      <RouterLink class="ghost link-button" to="/login">去登录</RouterLink>
    </div>
  </section>

  <section v-else-if="!isParent" class="hero">
    <p class="eyebrow">Parent View</p>
    <h1>家长视图</h1>
    <p class="lead">当前非家长身份，无法查看家长周报与绑定信息。</p>
    <div class="hero-actions">
      <RouterLink class="ghost link-button" to="/">返回首页</RouterLink>
      <RouterLink class="ghost link-button" to="/dashboard">查看学习仪表盘</RouterLink>
    </div>
  </section>

  <template v-else>
    <section class="hero">
      <p class="eyebrow">Parent View</p>
      <h1>家长视图 · 一键掌握孩子的学习脉搏</h1>
      <p class="lead">
        自动生成学习周报、弱项提示与鼓励文案，帮助家长精准陪伴，不打扰孩子节奏。
      </p>
      <div class="hero-actions">
        <RouterLink class="ghost link-button" to="/">返回首页</RouterLink>
        <RouterLink class="ghost link-button" to="/dashboard">查看学习仪表盘</RouterLink>
      </div>
      <div class="child-switcher">
        <label for="child">选择学生</label>
        <select id="child">
          <option v-for="c in children" :key="c.id">{{ c.display_name }}</option>
          <option v-if="!children.length">暂无绑定学生</option>
        </select>
      </div>

      <div class="binding">
      <p class="binding-title">绑定学生</p>
      <div class="binding-grid">
        <div class="binding-box">
          <p class="binding-label">通过邮箱直接绑定（无需确认）</p>
          <div class="row">
            <input v-model="emailToBind" type="email" placeholder="学生登录邮箱" />
            <button type="button" class="ghost" @click="bindByEmail">绑定</button>
          </div>
        </div>
        <div class="binding-box">
          <p class="binding-label">通过昵称发起请求（学生同意后生效）</p>
          <div class="row">
            <input v-model="nicknameToBind" type="text" placeholder="学生昵称（或用户名）" />
            <button type="button" class="ghost" @click="requestByNickname">发送请求</button>
          </div>
        </div>
      </div>
      <p v-if="message" class="status success">{{ message }}</p>
      <p v-if="error" class="status error">{{ error }}</p>

      <div class="children">
        <p class="binding-label">已绑定的学生</p>
        <p v-if="loadingChildren" class="muted">加载中…</p>
        <ul v-else>
          <li v-for="c in children" :key="c.id">{{ c.display_name }} · {{ c.email }}</li>
          <li v-if="!children.length" class="muted">暂无绑定学生</li>
        </ul>
      </div>
    </div>
  </section>

  <section class="report">
    <header>
      <p class="eyebrow">学习周报</p>
      <h2>第 {{ weeklyReport.week || '近 7 天' }} 周</h2>
    </header>
    <div class="report-grid">
      <article>
        <p class="report-label">数据概览</p>
        <p v-if="reportLoading" class="report-text">正在生成周报…</p>
        <p v-else-if="reportError" class="report-text status error">{{ reportError }}</p>
        <p v-else class="report-text">{{ weeklyReport.overallTone || '暂无数据概览' }}</p>
      </article>
      <article>
        <p class="report-label">AI 学习教练</p>
        <p v-if="reportLoading" class="report-text">正在生成周报…</p>
        <p v-else-if="reportError" class="report-text status error">{{ reportError }}</p>
        <p v-else class="report-text">{{ weeklyReport.aiSummary || '暂无 AI 总结' }}</p>
      </article>
      <article>
        <p class="report-label">鼓励话术</p>
        <p v-if="reportLoading" class="report-text">正在生成周报…</p>
        <p v-else-if="reportError" class="report-text status error">{{ reportError }}</p>
        <p v-else class="report-text">{{ weeklyReport.encouragement || '暂无鼓励话术' }}</p>
      </article>
    </div>
  </section>

  <section class="weakness">
    <header>
      <p class="eyebrow">薄弱项分析</p>
      <h2>知识点提醒</h2>
    </header>
    <div class="weak-grid">
      <article v-for="topic in weakTopics" :key="topic.subject">
        <p class="weak-subject">{{ topic.subject }}</p>
        <p class="weak-issue">当前表现：{{ topic.issue }}</p>
        <p class="weak-suggestion">建议：{{ topic.suggestion }}</p>
      </article>
      <article v-if="!reportLoading && !weakTopics.length">
        <p class="weak-subject">暂无薄弱项</p>
        <p class="weak-issue">请先完成一次错题录入或笔记转写</p>
        <p class="weak-suggestion">系统会在此给出建议</p>
      </article>
    </div>
  </section>

  <section class="highlights">
    <header>
      <p class="eyebrow">亮点 & 情绪</p>
      <h2>老师/家长一眼掌握</h2>
    </header>
    <div class="highlight-grid">
      <article v-for="card in highlightCards" :key="card.title">
        <p class="highlight-title">{{ card.title }}</p>
        <p class="highlight-detail">{{ card.detail }}</p>
      </article>
      <article v-if="!reportLoading && !highlightCards.length">
        <p class="highlight-title">暂无数据</p>
        <p class="highlight-detail">请先完成一次错题录入或笔记转写</p>
      </article>
    </div>
  </section>
  </template>
</template>

<style scoped>
section {
  background: rgba(255, 255, 255, 0.92);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  margin-bottom: 2rem;
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
}

.hero h1 {
  font-size: clamp(2rem, 4vw, 3rem);
  color: #0f1d40;
  margin-bottom: 1rem;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-bottom: 1.5rem;
}

.child-switcher {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-weight: 600;
  margin-bottom: 1rem;
}

.child-switcher select {
  border-radius: 999px;
  border: 1px solid rgba(15, 39, 88, 0.2);
  padding: 0.4rem 1rem;
  background: rgba(255, 255, 255, 0.9);
}

.report-grid,
.weak-grid,
.highlight-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem;
}

.report-grid article,
.weak-grid article,
.highlight-grid article {
  border-radius: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  padding: 1.2rem;
  background: rgba(248, 251, 255, 0.95);
}

.report-label {
  font-size: 0.85rem;
  color: rgba(20, 40, 70, 0.6);
}

.report-text {
  font-size: 1rem;
  color: rgba(16, 34, 68, 0.82);
}

.weak-subject {
  font-weight: 600;
  color: #0b2a6b;
}

.weak-issue,
.weak-suggestion {
  color: rgba(16, 34, 68, 0.76);
  margin-top: 0.4rem;
}

.highlight-title {
  font-weight: 600;
  color: #111b2e;
}

.highlight-detail {
  color: rgba(16, 34, 68, 0.8);
}

.binding {
  margin-top: 1.2rem;
  border-radius: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  padding: 1.2rem;
  background: rgba(248, 251, 255, 0.95);
}

.binding-title {
  font-weight: 700;
  color: #0f1d40;
  margin-bottom: 0.8rem;
}

.binding-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1rem;
}

.binding-box {
  border-radius: 1.2rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  padding: 1rem;
  background: rgba(255, 255, 255, 0.9);
}

.binding-label {
  font-size: 0.9rem;
  color: rgba(16, 34, 68, 0.8);
}

.row {
  display: flex;
  gap: 0.6rem;
  margin-top: 0.6rem;
}

.row input {
  flex: 1;
  border-radius: 999px;
  border: 1px solid rgba(15, 39, 88, 0.2);
  padding: 0.55rem 0.9rem;
  background: rgba(255, 255, 255, 0.95);
}

.children {
  margin-top: 1rem;
}

.children ul {
  list-style: none;
  padding: 0;
  margin: 0.6rem 0 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.muted {
  color: rgba(16, 34, 68, 0.7);
}

.status {
  font-weight: 600;
  margin-top: 0.6rem;
}

.status.success {
  color: #0a8060;
}

.status.error {
  color: #c44545;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}

.lead {
  color: rgba(16, 34, 68, 0.78);
}

.ghost.link-button {
  border: 1px solid rgba(17, 62, 125, 0.25);
  color: #0c2b53;
  padding: 0.75rem 1.6rem;
  border-radius: 999px;
  text-decoration: none;
}

@media (max-width: 640px) {
  section {
    border-radius: 1.4rem;
  }
}
</style>
