<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import Chart from 'chart.js/auto'
import { useAuthStore } from '../stores/auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const auth = useAuthStore()
const router = useRouter()

const loading = ref(false)
const errorMessage = ref('')
const dashboard = ref(null)

const timeChartRef = ref(null)
const masteryChartRef = ref(null)
const charts = {
  time: null,
  mastery: null,
}

const cleanupCharts = () => {
  Object.values(charts).forEach((instance) => instance?.destroy())
  charts.time = null
  charts.mastery = null
}

const formatShortDate = (iso) => {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      month: '2-digit',
      day: '2-digit',
    }).format(d)
  } catch {
    return ''
  }
}

const formatDateTimeCN = (iso) => {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(d)
  } catch {
    return ''
  }
}

const classroomNotes = computed(() => dashboard.value?.classroom_records?.items || [])

const summaryCards = computed(() => {
  const eb = dashboard.value?.error_book
  const totals = eb?.totals
  const daily = eb?.daily_counts || []
  const weekNew = daily.reduce((acc, item) => acc + Number(item?.count || 0), 0)
  const topSubject = eb?.subjects?.[0]?.subject || '未分类'
  const nextReview = eb?.top_review_plan?.[0] || '暂无复习建议'
  return {
    weekNew,
    totalEntries: totals?.total_entries ?? 0,
    topSubject,
    nextReview,
  }
})

const reminders = computed(() => {
  const items = []
  const eb = dashboard.value?.error_book
  const insights = dashboard.value?.insights || []
  insights.forEach((text, i) => {
    items.push({ id: `insight-${i}`, title: '仪表盘总结', detail: text })
  })
  if (eb?.weak_concepts?.length) {
    items.push({ id: 'weak', title: '薄弱知识点', detail: eb.weak_concepts.join('、') })
  }
  if (eb?.top_key_points?.length) {
    items.push({ id: 'kp', title: '高频要点', detail: eb.top_key_points.slice(0, 6).join('；') })
  }
  if (eb?.top_review_plan?.length) {
    items.push({ id: 'rp', title: '复习建议', detail: eb.top_review_plan.slice(0, 4).join('；') })
  }
  return items.slice(0, 6)
})

const recentErrorBook = computed(() => dashboard.value?.error_book?.recent_entries || [])

const goToErrorEntry = (id) => {
  const entryId = Number(id)
  if (!entryId) return
  router.push({ path: '/error-book', query: { entry_id: String(entryId) } })
}

const renderCharts = () => {
  cleanupCharts()
  const eb = dashboard.value?.error_book
  const daily = eb?.daily_counts || []
  const subjects = eb?.subjects || []

  if (timeChartRef.value) {
    const labels = daily.map((item) => formatShortDate(item?.date))
    const data = daily.map((item) => Number(item?.count || 0))
    charts.time = new Chart(timeChartRef.value, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: '新增错题 (条)',
            data,
            backgroundColor: 'rgba(111, 157, 236, 0.65)',
            borderRadius: 10,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, grid: { color: 'rgba(15,39,88,0.08)' }, ticks: { precision: 0 } },
        },
      },
    })
  }

  if (masteryChartRef.value) {
    const top = subjects.slice(0, 6)
    const labels = top.map((item) => item.subject)
    const data = top.map((item) => Number(item.count || 0))
    charts.mastery = new Chart(masteryChartRef.value, {
      type: 'radar',
      data: {
        labels,
        datasets: [
          {
            label: '错题分布（近80条窗口）',
            data,
            backgroundColor: 'rgba(139, 212, 200, 0.3)',
            borderColor: 'rgba(139, 212, 200, 0.9)',
            borderWidth: 2,
            pointRadius: 3,
          },
        ],
      },
      options: {
        responsive: true,
        scales: {
          r: {
            beginAtZero: true,
            angleLines: { color: 'rgba(15,39,88,0.15)' },
            grid: { color: 'rgba(15,39,88,0.1)' },
            ticks: { display: false },
          },
        },
      },
    })
  }
}

const loadDashboard = async () => {
  if (!auth.token) {
    router.push('/login')
    return
  }

  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(`${API_BASE_URL}/api/dashboard/summary`, {
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401) {
        auth.logout()
        router.push('/login')
        throw new Error('登录已过期，请重新登录')
      }
      throw new Error(data?.message || '获取学习仪表盘失败')
    }
    dashboard.value = data
    await nextTick()
    renderCharts()
  } catch (err) {
    errorMessage.value = err?.message || '获取学习仪表盘失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadDashboard()
})

onBeforeUnmount(() => {
  cleanupCharts()
})
</script>

<template>
  <section class="hero">
    <p class="eyebrow">Learning Dashboard</p>
    <h1>学习仪表盘 · 数据驱动的自我管理</h1>
    <p class="lead">
      聚合学习日志、复习节奏与知识掌握度，自动生成提醒与激励，让复习节奏更可视、更可控。
    </p>
    <p v-if="loading" class="lead">正在加载仪表盘数据...</p>
    <p v-else-if="errorMessage" class="lead">{{ errorMessage }}</p>
    <div class="hero-actions">
      <RouterLink class="ghost link-button" to="/">返回首页</RouterLink>
      <RouterLink class="ghost link-button" to="/error-book">查看错题本</RouterLink>
    </div>
    <div class="summary">
      <article>
        <p class="label">近 7 天新增错题</p>
        <p class="value">{{ summaryCards.weekNew }} 条</p>
        <small>来自错题本上传记录</small>
      </article>
      <article>
        <p class="label">错题总数</p>
        <p class="value">{{ summaryCards.totalEntries }} 条</p>
        <small>已累计沉淀的学习内容</small>
      </article>
      <article>
        <p class="label">高频科目</p>
        <p class="value">{{ summaryCards.topSubject }}</p>
        <small>用于快速定位复习方向</small>
      </article>
      <article>
        <p class="label">优先复习建议</p>
        <p class="value">{{ summaryCards.nextReview }}</p>
        <small>从 AI 分析中抽取（若有）</small>
      </article>
    </div>
  </section>

  <section class="classroom">
    <header>
      <p class="eyebrow">课堂记录</p>
      <h2>课堂笔记 / 课堂记录</h2>
      <p class="lead">{{ dashboard?.classroom_records?.message || '课堂记录模块开发中。' }}</p>
    </header>

    <div v-if="classroomNotes.length" class="recent-grid">
      <article v-for="item in classroomNotes" :key="item.id" class="recent-item">
        <p class="recent-title">{{ item.title || '未命名笔记' }}</p>
        <p class="recent-meta">
          {{ item.subject || '未分类' }} · {{ formatDateTimeCN(item.updated_at || item.created_at) }}
        </p>
        <p class="recent-detail">{{ item.transcript_preview }}</p>
      </article>
    </div>
  </section>

  <section class="charts">
    <article>
      <header>
        <p class="eyebrow">学习时长</p>
        <h2>近 7 天错题新增</h2>
      </header>
      <canvas ref="timeChartRef" aria-label="学习时长柱状图" role="img"></canvas>
    </article>
    <article>
      <header>
        <p class="eyebrow">掌握度</p>
        <h2>科目错题分布</h2>
      </header>
      <canvas ref="masteryChartRef" aria-label="掌握度雷达图" role="img"></canvas>
    </article>
  </section>

  <section class="reminders">
    <header>
      <p class="eyebrow">智能提醒</p>
      <h2>让复习节奏始终在线</h2>
    </header>
    <div class="reminder-grid">
      <article v-for="item in reminders" :key="item.id">
        <p class="reminder-title">{{ item.title }}</p>
        <p class="reminder-detail">{{ item.detail }}</p>
      </article>
    </div>
  </section>

  <section class="queue">
    <header>
      <p class="eyebrow">错题本</p>
      <h2>最近的学习内容（来自错题本）</h2>
      <p>点击进入错题本可查看详情与练习题。</p>
    </header>
    <div class="queue-list">
      <article
        v-for="item in recentErrorBook"
        :key="item.id"
        class="queue-item"
        role="button"
        tabindex="0"
        @click="goToErrorEntry(item.id)"
        @keydown.enter.prevent="goToErrorEntry(item.id)"
        @keydown.space.prevent="goToErrorEntry(item.id)"
      >
        <div>
          <p class="queue-title">{{ item.title }}</p>
          <p class="queue-next">创建时间：{{ formatShortDate(item.created_at) }}</p>
        </div>
        <span class="queue-priority">{{ item.subject || '未分类' }}</span>
      </article>
      <article v-if="!recentErrorBook.length">
        <div>
          <p class="queue-title">暂无错题记录</p>
          <p class="queue-next">先去错题本上传一张错题图片。</p>
        </div>
      </article>
    </div>
  </section>
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

.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
}

.summary article {
  border-radius: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.18);
  padding: 1rem;
  background: rgba(248, 251, 255, 0.95);
}

.label {
  font-size: 0.85rem;
  color: rgba(20, 40, 70, 0.6);
}

.value {
  font-size: 1.6rem;
  font-weight: 700;
  color: #0f1d40;
}

.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
}

.charts article {
  border-radius: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.18);
  padding: 1.5rem;
  background: rgba(255, 255, 255, 0.95);
}

canvas {
  width: 100%;
  max-height: 320px;
}

.reminder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 1rem;
}

.reminder-grid article {
  border-radius: 1.2rem;
  border: 1px solid rgba(139, 212, 200, 0.4);
  padding: 1rem;
  background: rgba(139, 212, 200, 0.1);
}

.reminder-title {
  font-weight: 600;
  color: #0b2a6b;
}

.reminder-detail {
  color: rgba(16, 34, 68, 0.76);
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.queue-list article {
  border-radius: 1.2rem;
  border: 1px solid rgba(111, 157, 236, 0.18);
  padding: 1rem 1.2rem;
  background: rgba(255, 255, 255, 0.95);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.6rem;
  cursor: pointer;
  transition: border 0.2s ease, box-shadow 0.2s ease;
}

.queue-list article:hover {
  border-color: rgba(111, 157, 236, 0.55);
  box-shadow: 0 10px 26px rgba(13, 52, 104, 0.12);
}

.queue-title {
  font-weight: 600;
  color: #111b2e;
}

.queue-priority {
  border-radius: 999px;
  padding: 0.3rem 0.9rem;
  font-weight: 600;
  font-size: 0.85rem;
  background: rgba(111, 157, 236, 0.12);
  color: #0c2b53;
}

.queue-priority[data-level='高'] {
  background: rgba(255, 122, 122, 0.15);
  color: #b03030;
}

.queue-priority[data-level='中'] {
  background: rgba(255, 193, 79, 0.2);
  color: #b05d00;
}

.queue-priority[data-level='低'] {
  background: rgba(139, 212, 200, 0.2);
  color: #0a594d;
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
