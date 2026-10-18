<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import MathText from '../components/MathText.vue'

const cameraActive = ref(false)
const videoRef = ref(null)
const streamRef = ref(null)
const uploadPreview = ref(null)
const uploadLabel = ref('暂未上传图片')
const notification = ref('')

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()
const isAuthenticated = computed(() => authStore.isAuthenticated)

const isMobile = ref(false)
let mobileMql = null
let mobileListener = null

const loading = ref(false)
const entries = ref([])
const selectedEntry = ref(null)
const detailRef = ref(null)
const selectedEntryImageUrl = ref('')
const selectedEntryImageLoading = ref(false)
const selectedEntryImageError = ref('')

const uploadProgress = ref({ state: 'idle', message: '', detail: '' })

const quizLoading = ref(false)
const quizError = ref('')
const quiz = ref(null)
const quizSelected = ref(null)
const quizChecked = ref(false)
const confettiKey = ref(0)
const confettiPieces = ref([])

const historyExpanded = ref(false)
const maxHistoryItems = 5

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const setNotification = (message) => {
  notification.value = message
  if (message) {
    setTimeout(() => (notification.value = ''), 4000)
  }
}

const authedFetch = async (path, options = {}) => {
  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${authStore.token}`)
  const resp = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
  if (resp.status === 401) {
    authStore.logout()
    throw new Error('登录已过期，请重新登录')
  }
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) {
    throw new Error(data?.message || '请求失败')
  }
  return data
}

const authedFetchBlob = async (path, options = {}) => {
  const headers = new Headers(options.headers || {})
  headers.set('Authorization', `Bearer ${authStore.token}`)
  const resp = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
  if (resp.status === 401) {
    authStore.logout()
    throw new Error('登录已过期，请重新登录')
  }
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(data?.message || '图片请求失败')
  }
  return await resp.blob()
}

const revokeSelectedEntryImageUrl = () => {
  if (selectedEntryImageUrl.value) {
    URL.revokeObjectURL(selectedEntryImageUrl.value)
    selectedEntryImageUrl.value = ''
  }
}

const loadEntryImage = async (entry) => {
  revokeSelectedEntryImageUrl()
  selectedEntryImageError.value = ''

  if (!entry?.image_url) return
  if (!isAuthenticated.value) return

  selectedEntryImageLoading.value = true
  try {
    const blob = await authedFetchBlob(entry.image_url)
    selectedEntryImageUrl.value = URL.createObjectURL(blob)
  } catch (err) {
    selectedEntryImageError.value = err?.message || '图片加载失败'
  } finally {
    selectedEntryImageLoading.value = false
  }
}

const stripCodeFence = (text) => {
  if (!text) return ''
  const trimmed = String(text).trim()
  const fence = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i)
  return fence ? fence[1].trim() : trimmed
}

const parseAiAnalysis = (text) => {
  const cleaned = stripCodeFence(text)
  if (!cleaned) return { ok: false, raw: '' }
  try {
    return { ok: true, raw: cleaned, json: JSON.parse(cleaned) }
  } catch {
    return { ok: false, raw: cleaned }
  }
}

const aiView = computed(() => parseAiAnalysis(selectedEntry.value?.ai_analysis))

const normalizedMistakes = computed(() => {
  const raw = aiView.value?.json?.mistakes
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => {
      if (typeof item === 'string') {
        const text = item.trim()
        return text ? { concept: '', reason: text, correct_approach: '', practice: '', evidence: '' } : null
      }
      if (item && typeof item === 'object') {
        const concept = String(item.concept || '').trim()
        const reason = String(item.reason || '').trim()
        const correct_approach = String(item.correct_approach || '').trim()
        const practice = String(item.practice || '').trim()
        const evidence = String(item.evidence || '').trim()
        if (!concept && !reason && !correct_approach && !practice && !evidence) return null
        return { concept, reason, correct_approach, practice, evidence }
      }
      return null
    })
    .filter(Boolean)
})

const showMistakes = computed(() => {
  const items = normalizedMistakes.value
  if (!items.length) return false
  // 如果只有占位式的“未标注知识点/无内容”，则不显示
  const meaningful = items.filter((m) => {
    const joined = [m.concept, m.reason, m.correct_approach, m.practice, m.evidence].join(' ').trim()
    if (!joined) return false
    if (joined === '未标注知识点') return false
    return true
  })
  return meaningful.length > 0
})

const resetQuizState = () => {
  quizLoading.value = false
  quizError.value = ''
  quiz.value = null
  quizSelected.value = null
  quizChecked.value = false
}

const applyQuizFromEntry = (entry) => {
  quizError.value = ''
  quiz.value = null
  quizSelected.value = null
  quizChecked.value = false
  confettiKey.value = 0
  confettiPieces.value = []

  const q = entry?.quiz
  if (q && typeof q === 'object' && Array.isArray(q.options) && q.options.length === 4) {
    quiz.value = q
  } else {
    quiz.value = null
  }

  if (entry?.quiz_error) {
    quizError.value = String(entry.quiz_error)
  }
}

const visibleEntries = computed(() => {
  if (historyExpanded.value) return entries.value
  return entries.value.slice(0, maxHistoryItems)
})

const canExpandHistory = computed(() => entries.value.length > maxHistoryItems)

const cleanText = (value) => {
  if (value == null) return ''
  return String(value)
    .replace(/[`]/g, '')
    .replace(/[‘’]/g, '')
    .replace(/\u00a0/g, ' ')
    .trim()
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
      hour12: false,
    }).format(d)
  } catch {
    return ''
  }
}

const loadEntries = async () => {
  if (!isAuthenticated.value) return
  loading.value = true
  try {
    entries.value = await authedFetch('/api/error-book/entries')
  } catch (err) {
    setNotification(err.message)
    if (err?.message?.includes('登录已过期')) {
      router.push('/login')
    }
  } finally {
    loading.value = false
  }
}

const loadEntryDetail = async (entryId) => {
  if (!isAuthenticated.value) return
  try {
    quizLoading.value = true
    quizError.value = ''
    quiz.value = null
    selectedEntry.value = await authedFetch(`/api/error-book/entries/${entryId}`)
    await loadEntryImage(selectedEntry.value)
    applyQuizFromEntry(selectedEntry.value)

    try {
      router.replace({
        path: '/error-book',
        query: { ...route.query, entry_id: String(entryId) },
      })
    } catch {
      // ignore
    }
  } catch (err) {
    setNotification(err.message)
    if (err?.message?.includes('登录已过期')) {
      router.push('/login')
    }
  } finally {
    quizLoading.value = false
  }
}

const openEntryAndScroll = async (entryId) => {
  await loadEntryDetail(entryId)
  await nextTick()
  try {
    detailRef.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  } catch {
    // ignore
  }
}

const deleteEntry = async (entryId) => {
  if (!isAuthenticated.value) return
  const ok = window.confirm('确定要删除这条错题记录吗？此操作不可恢复。')
  if (!ok) return

  try {
    await authedFetch(`/api/error-book/entries/${entryId}`, { method: 'DELETE' })
    entries.value = entries.value.filter((item) => item.id !== entryId)
    if (selectedEntry.value?.id === entryId) {
      selectedEntry.value = null
      revokeSelectedEntryImageUrl()
      resetQuizState()
    }
    setNotification('已删除')
  } catch (err) {
    setNotification(err?.message || '删除失败')
  }
}

const checkQuiz = () => {
  quizChecked.value = true

  const correct = quizSelected.value === quiz.value?.answer_index
  if (correct) {
    // Trigger a one-shot celebration animation
    const palette = ['#6f9dec', '#8bd4c8', 'rgba(255, 212, 218, 0.95)']
    const pieces = Array.from({ length: 22 }).map((_, i) => {
      const left = 44 + (Math.random() - 0.5) * 28 // around center
      const delay = Math.random() * 0.12
      const duration = 0.9 + Math.random() * 0.6
      const size = 6 + Math.random() * 8
      const rotate = Math.floor(Math.random() * 180)
      const dx = (Math.random() - 0.5) * 220
      const dy = -220 - Math.random() * 180
      const color = palette[i % palette.length]
      return { left, delay, duration, size, rotate, dx, dy, color }
    })
    confettiPieces.value = pieces
    confettiKey.value = Date.now()
  }
}

const uploadEntry = async (file, sourceLabel) => {
  if (!isAuthenticated.value) {
    setNotification('请先登录后再使用错题解析功能。')
    return
  }

  const form = new FormData()
  form.append('image', file)

  loading.value = true
  uploadProgress.value = { state: 'processing', message: '处理中…正在进行 OCR + AI 分析', detail: '' }
  try {
    setNotification('已上传，正在进行 OCR + AI 分析...')
    const created = await authedFetch('/api/error-book/entries', {
      method: 'POST',
      body: form,
    })
    await loadEntries()
    await loadEntryDetail(created.id)
    const statusText = String(created?.status || '')
    const verdictText = String(created?.verdict || '')

    if (statusText.includes('failed')) {
      uploadProgress.value = {
        state: 'error',
        message: '识别失败',
        detail: verdictText || statusText,
      }
    } else {
      uploadProgress.value = {
        state: 'success',
        message: '已完成',
        detail: statusText ? `状态：${statusText}` : '',
      }
    }

    setNotification(`解析完成：${created.status}`)
  } catch (err) {
    setNotification(err.message)
    uploadProgress.value = { state: 'error', message: '识别失败', detail: err?.message || '' }
    if (err?.message?.includes('登录已过期')) {
      router.push('/login')
    }
  } finally {
    loading.value = false
  }
}

const startCamera = async () => {
  if (!navigator.mediaDevices?.getUserMedia) {
    setNotification('当前浏览器不支持摄像头调用，可改用图片上传。')
    return
  }
  try {
    streamRef.value = await navigator.mediaDevices.getUserMedia({ video: true })
    if (videoRef.value) {
      videoRef.value.srcObject = streamRef.value
      await videoRef.value.play()
    }
    cameraActive.value = true
    setNotification('摄像头已开启，可以对准错题拍摄。')
  } catch (error) {
    setNotification('无法开启摄像头，已自动切换为图片上传方式。')
    console.error(error)
  }
}

const stopCamera = () => {
  streamRef.value?.getTracks().forEach((track) => track.stop())
  streamRef.value = null
  cameraActive.value = false
}

const toggleCamera = () => {
  if (cameraActive.value) {
    stopCamera()
  } else {
    startCamera()
  }
}

const captureSnapshot = () => {
  if (!cameraActive.value || !videoRef.value) {
    setNotification('请先开启摄像头再进行拍摄。')
    return
  }
  const canvas = document.createElement('canvas')
  canvas.width = videoRef.value.videoWidth
  canvas.height = videoRef.value.videoHeight
  const context = canvas.getContext('2d')
  context.drawImage(videoRef.value, 0, 0, canvas.width, canvas.height)
  uploadPreview.value = canvas.toDataURL('image/png')
  uploadLabel.value = '已截取课堂画面'

  canvas.toBlob((blob) => {
    if (!blob) {
      setNotification('截取失败，请重试')
      return
    }
    const file = new File([blob], `capture-${Date.now()}.png`, { type: 'image/png' })
    uploadEntry(file, 'camera')
  }, 'image/png')
}

const handleFileSelect = (event) => {
  const file = event.target.files?.[0]
  if (!file) return
  uploadLabel.value = file.name
  const reader = new FileReader()
  reader.onload = (e) => {
    uploadPreview.value = e.target?.result
  }
  reader.readAsDataURL(file)
  uploadEntry(file, 'upload')
}

const handleDrop = (event) => {
  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  if (!file.type?.startsWith('image/')) {
    setNotification('请拖入图片文件（jpg/png/webp 等）。')
    return
  }
  uploadLabel.value = file.name
  const reader = new FileReader()
  reader.onload = (e) => {
    uploadPreview.value = e.target?.result
  }
  reader.readAsDataURL(file)
  uploadEntry(file, 'drop')
}

onBeforeUnmount(() => {
  if (cameraActive.value) {
    stopCamera()
  }
  revokeSelectedEntryImageUrl()

  if (mobileMql && mobileListener) {
    try {
      mobileMql.removeEventListener('change', mobileListener)
    } catch {
      mobileMql.removeListener?.(mobileListener)
    }
  }
})

onMounted(async () => {
  if (typeof window !== 'undefined' && window.matchMedia) {
    mobileMql = window.matchMedia('(max-width: 640px)')
    const sync = () => {
      isMobile.value = Boolean(mobileMql?.matches)
    }
    mobileListener = sync
    sync()
    try {
      mobileMql.addEventListener('change', sync)
    } catch {
      mobileMql.addListener?.(sync)
    }
  }

  if (authStore.token) {
    try {
      await authStore.fetchProfile()
    } catch (err) {
      setNotification(err?.message || '登录已过期，请重新登录')
      router.push('/login')
      return
    }
  }

  if (isAuthenticated.value) {
    await loadEntries()

    const q = route.query?.entry_id
    const id = Array.isArray(q) ? q[0] : q
    const entryId = Number(id)
    if (entryId) {
      await openEntryAndScroll(entryId)
    }
  }
})

watch(
  () => selectedEntry.value,
  async () => {},
  { immediate: false }
)
</script>

<template>
  <section class="hero">
    <p class="eyebrow">Error Book Manager</p>
    <h1>错题本 · 让每一次失误都变成洞察</h1>
    <p class="lead">
      支持摄像头拍摄或上传图片，自动识别题目、定位错因，并在历史记录中聚合分析结果。
    </p>
    <div class="hero-actions">
      <RouterLink class="ghost link-button" to="/">返回首页</RouterLink>
      <RouterLink v-if="!isAuthenticated" class="secondary link-button" to="/login">登录后使用</RouterLink>
      <button class="primary" type="button" @click="toggleCamera">
        {{ cameraActive ? '关闭摄像头' : '一键开启摄像头' }}
      </button>
      <label class="secondary file-label">
        <input type="file" accept="image/*" capture="environment" @change="handleFileSelect" />
        上传错题图片
      </label>
    </div>
    <p v-if="notification" class="notification">{{ notification }}</p>
    <p v-if="loading" class="notification">处理中，请稍等…</p>
  </section>

  <section class="capture-grid">
    <article class="card camera-card">
      <header>
        <p class="eyebrow">实时拍摄</p>
        <h2>课堂摄像头</h2>
      </header>
      <div class="camera-wrapper" :class="{ inactive: !cameraActive }">
        <video ref="videoRef" autoplay playsinline muted></video>
        <p v-if="!cameraActive" class="placeholder">开启摄像头后即可实时预览</p>
      </div>
      <div class="card-actions">
        <button class="ghost" type="button" @click="toggleCamera">
          {{ cameraActive ? '结束拍摄' : '开始拍摄' }}
        </button>
        <button class="primary" type="button" @click="captureSnapshot">截取并分析</button>
      </div>
    </article>

    <article class="card upload-card">
      <header>
        <p class="eyebrow">上传/拖拽</p>
        <h2>已有错题图片</h2>
      </header>
      <label class="upload-area" @dragenter.prevent @dragover.prevent @drop.prevent="handleDrop">
        <input type="file" accept="image/*" @change="handleFileSelect" />
        <p>拖入或点击上传题目图片</p>
        <small>{{ uploadLabel }}</small>
      </label>
      <p>系统将自动进行 OCR + LLM 解析，并标记错因标签。</p>
    </article>
  </section>

  <section class="preview" v-if="uploadPreview">
    <header>
      <p class="eyebrow">最近一次采集</p>
      <h2>预览与进度</h2>
    </header>
    <div class="preview-content">
      <img :src="uploadPreview" alt="错题图片预览" />
      <div class="preview-status">
        <p>
          解析状态：
          <span
            class="status-pill"
            :data-state="uploadProgress.state"
          >
            {{ uploadProgress.message || (loading ? '处理中…' : '等待操作') }}
          </span>
        </p>
        <p v-if="uploadProgress.state === 'processing'">预计 30 秒内即可在历史记录看到结果。</p>
        <p v-else-if="uploadProgress.state === 'success'">{{ uploadProgress.detail || '已生成 OCR 与 AI 分析结果。' }}</p>
        <p v-else-if="uploadProgress.state === 'error'">原因：{{ uploadProgress.detail || '未知错误' }}</p>
      </div>
    </div>
  </section>

  <section class="history">
    <header>
      <p class="eyebrow">历史记录</p>
      <h2>错题诊断档案</h2>
      <p>自动同步错因标签与复习建议，可据此生成练习或提醒家长。</p>
    </header>
    <div class="history-list">
      <article
        v-for="item in visibleEntries"
        :key="item.id"
        class="history-item"
        role="button"
        tabindex="0"
        @click="openEntryAndScroll(item.id)"
        @keydown.enter.prevent="openEntryAndScroll(item.id)"
        @keydown.space.prevent="openEntryAndScroll(item.id)"
      >
        <div>
          <p class="history-title">{{ item.title }}</p>
          <p class="history-meta">{{ item.subject }} · {{ formatDateTimeCN(item.created_at) }}</p>
        </div>
        <div class="history-right">
          <p class="history-status">{{ item.status }}</p>
          <button class="danger" type="button" @click.stop="deleteEntry(item.id)">删除</button>
        </div>
        <p class="history-verdict">{{ item.verdict }}</p>
        <p class="history-hint">点击查看解析 →</p>
      </article>

      <div v-if="canExpandHistory" class="history-more">
        <button class="ghost" type="button" @click="historyExpanded = !historyExpanded">
          {{ historyExpanded ? '收起' : `展开更多（${entries.length - maxHistoryItems} 条）` }}
        </button>
      </div>

      <p v-if="!entries.length && isAuthenticated" class="history-empty">暂无记录，上传一张错题图片开始吧。</p>
    </div>
  </section>

  <section class="detail" v-if="selectedEntry" ref="detailRef">
    <header>
      <p class="eyebrow">解析详情</p>
      <h2>{{ selectedEntry.title }}</h2>
      <p>{{ selectedEntry.subject }} · {{ selectedEntry.status }}</p>
    </header>

    <div class="detail-grid">
      <div class="detail-media">
          <div class="panel detail-image">
          <p v-if="selectedEntryImageLoading" class="image-note">图片加载中…</p>
          <p v-else-if="selectedEntryImageError" class="image-note">{{ selectedEntryImageError }}</p>
            <template v-else>
              <img v-if="selectedEntryImageUrl" class="detail-img" :src="selectedEntryImageUrl" alt="错题图片" />
            </template>
        </div>

        <details class="panel" :open="!isMobile">
          <summary class="panel-title">OCR 文本</summary>
          <pre>{{ selectedEntry.ocr_text }}</pre>
        </details>
      </div>

      <details class="panel ai-panel" :open="!isMobile">
        <summary class="panel-title">AI 分析</summary>
        <div v-if="aiView.ok" class="analysis">
          <div class="analysis-badges">
            <span v-if="aiView.json?.verdict" class="badge">结论：{{ aiView.json.verdict }}</span>
            <span v-if="aiView.json?.subject" class="badge">科目：{{ aiView.json.subject }}</span>
            <span v-if="aiView.json?.confidence != null" class="badge">置信度：{{ aiView.json.confidence }}</span>
          </div>

          <div class="analysis-sections">
            <details v-if="showMistakes" class="analysis-section" :open="!isMobile">
              <summary>错因拆解</summary>
              <ul>
                <li v-for="(m, idx) in normalizedMistakes" :key="idx">
                  <strong v-if="m.concept">{{ cleanText(m.concept) }}</strong>
                  <span v-if="m.concept && m.reason">：{{ cleanText(m.reason) }}</span>
                  <span v-else-if="!m.concept && m.reason">{{ cleanText(m.reason) }}</span>
                  <div v-if="m.correct_approach" class="analysis-sub">正确做法：{{ cleanText(m.correct_approach) }}</div>
                  <div v-if="m.practice" class="analysis-sub">练习建议：{{ cleanText(m.practice) }}</div>
                  <div v-if="m.evidence" class="analysis-sub">证据：{{ cleanText(m.evidence) }}</div>
                </li>
              </ul>
            </details>

            <details v-if="aiView.json?.key_points?.length" class="analysis-section" :open="!isMobile">
              <summary>关键要点</summary>
              <ul>
                <li v-for="(p, idx) in aiView.json.key_points" :key="idx">
                  <MathText :text="cleanText(p)" />
                </li>
              </ul>
            </details>

            <details v-if="aiView.json?.review_plan?.length" class="analysis-section" :open="!isMobile">
              <summary>复习计划</summary>
              <ol>
                <li v-for="(s, idx) in aiView.json.review_plan" :key="idx">
                  <span v-if="s.day">第 {{ s.day }} 天：</span>
                  <span v-if="s.task">{{ s.task }}</span>
                  <span v-else>{{ typeof s === 'string' ? s : '' }}</span>
                  <span v-if="s.time" class="analysis-sub">时间：{{ s.time }}</span>
                </li>
              </ol>
            </details>

            <details
              v-if="!aiView.json?.mistakes && !aiView.json?.key_points && !aiView.json?.review_plan"
              class="analysis-section"
              :open="!isMobile"
            >
              <summary>原始 JSON</summary>
              <pre>{{ aiView.raw }}</pre>
            </details>
          </div>
        </div>
        <pre v-else>{{ aiView.raw || selectedEntry.ai_analysis }}</pre>
      </details>

      <p v-if="quizLoading" class="notification">练习题生成中…</p>
      <p v-else-if="quizError" class="notification quiz-error">练习题生成失败：{{ quizError }}</p>

      <details v-if="quiz" class="panel" :open="!isMobile">
        <summary class="panel-title">练习题（选择题）</summary>

        <div class="quiz">
          <div class="quiz-body">
            <div class="quiz-question">
              <MathText :text="cleanText(quiz.question)" />
            </div>
            <div class="quiz-options">
              <label v-for="(opt, idx) in quiz.options" :key="idx" class="quiz-option">
                <input type="radio" name="quiz" :value="idx" v-model.number="quizSelected" />
                <div class="quiz-option-text">
                  <MathText :text="cleanText(`${String.fromCharCode(65 + idx)}. ${opt}`)" />
                </div>
              </label>
            </div>

            <div class="quiz-actions">
              <button class="primary" type="button" :disabled="quizSelected == null" @click="checkQuiz">
                提交答案
              </button>
            </div>

            <div v-if="quizChecked" class="quiz-result" :data-ok="quizSelected === quiz.answer_index">
              <p class="quiz-result-title">
                {{ quizSelected === quiz.answer_index ? '回答正确' : '回答错误' }}
              </p>

              <div v-if="quizSelected === quiz.answer_index" class="quiz-celebrate" :key="confettiKey">
                <div class="quiz-confetti" aria-hidden="true">
                  <span
                    v-for="(p, i) in confettiPieces"
                    :key="i"
                    class="confetti-piece"
                    :style="{
                      left: p.left + '%',
                      width: p.size + 'px',
                      height: Math.max(10, p.size * 1.8) + 'px',
                      background: p.color,
                      transform: 'rotate(' + p.rotate + 'deg)',
                      animationDelay: p.delay + 's',
                      animationDuration: p.duration + 's',
                      '--dx': p.dx + 'px',
                      '--dy': p.dy + 'px',
                    }"
                  />
                </div>
                <p class="quiz-celebrate-title">太棒了！</p>
                <p class="quiz-celebrate-sub">继续保持这个手感。</p>
              </div>

              <p>正确答案：{{ String.fromCharCode(65 + quiz.answer_index) }}</p>
              <div v-if="quiz.explanation" class="quiz-explanation">
                <p>解析：</p>
                <MathText :text="cleanText(quiz.explanation)" />
              </div>
            </div>
          </div>
        </div>
      </details>
    </div>
  </section>
</template>

<style scoped>
section {
  background: rgba(255, 255, 255, 0.9);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  margin-bottom: 2rem;
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
}

.hero h1 {
  font-size: clamp(2rem, 4vw, 3rem);
  color: #0f1d40;
  margin-bottom: 0.75rem;
}

.lead {
  color: rgba(16, 34, 68, 0.78);
  max-width: 640px;
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.8rem;
  margin-bottom: 0.3rem;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  margin-top: 1.5rem;
}

.primary,
.secondary,
.ghost {
  border-radius: 999px;
  padding: 0.85rem 1.6rem;
  font-weight: 600;
  border: none;
  cursor: pointer;
}

.primary {
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
  box-shadow: 0 12px 30px rgba(85, 141, 214, 0.4);
}

.secondary,
.ghost {
  border: 1px solid rgba(17, 62, 125, 0.25);
  color: #0c2b53;
  background: rgba(255, 255, 255, 0.8);
}

.link-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}

.file-label input {
  display: none;
}

/* 上传时间已移除 */

.notification {
  margin-top: 1rem;
  color: #0c2b53;
  font-weight: 500;
}

.capture-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
}

.card {
  border-radius: 1.8rem;
  border: 1px solid rgba(111, 157, 236, 0.18);
  background: rgba(248, 251, 255, 0.95);
  padding: 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.camera-wrapper {
  position: relative;
  border-radius: 1.5rem;
  overflow: hidden;
  min-height: 260px;
  background: rgba(17, 31, 61, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
}

.camera-wrapper video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.placeholder {
  position: absolute;
  color: rgba(10, 25, 58, 0.6);
}

.camera-wrapper.inactive::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(111, 157, 236, 0.2), rgba(255, 255, 255, 0.4));
}

.card-actions {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

.upload-area {
  border: 1px dashed rgba(15, 39, 88, 0.3);
  border-radius: 1.5rem;
  padding: 2rem;
  text-align: center;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.7);
}

.upload-area input {
  display: none;
}

.preview img {
  width: min(320px, 100%);
  border-radius: 1.2rem;
  box-shadow: 0 15px 40px rgba(23, 41, 79, 0.2);
}

.preview-content {
  display: flex;
  gap: 1.5rem;
  align-items: center;
  flex-wrap: wrap;
}

.preview-status {
  min-width: min(420px, 100%);
}

.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 0.25rem 0.75rem;
  border-radius: 999px;
  font-weight: 700;
  border: 1px solid rgba(17, 62, 125, 0.25);
  background: rgba(255, 255, 255, 0.85);
}

.status-pill[data-state='success'] {
  color: #0a8060;
  border-color: rgba(10, 128, 96, 0.3);
}

.status-pill[data-state='error'] {
  color: #c44545;
  border-color: rgba(196, 69, 69, 0.3);
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.history-item {
  border-radius: 1.3rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  padding: 1rem 1.2rem;
  background: rgba(255, 255, 255, 0.95);
  display: grid;
  grid-template-columns: minmax(0, 2fr) auto;
  gap: 0.8rem;
  cursor: pointer;
  transition: transform 140ms ease, border-color 140ms ease, box-shadow 140ms ease;
}

.history-item:hover {
  border-color: rgba(111, 157, 236, 0.32);
  box-shadow: 0 18px 40px rgba(21, 47, 89, 0.1);
  transform: translateY(-1px);
}

.history-item:focus-visible {
  outline: 3px solid rgba(111, 157, 236, 0.35);
  outline-offset: 3px;
}

.history-title {
  font-weight: 600;
  color: #111b2e;
}

.history-meta {
  color: rgba(16, 34, 68, 0.64);
  font-size: 0.9rem;
}

.history-status {
  justify-self: end;
  font-weight: 600;
  color: #315aa6;
}

.history-right {
  justify-self: end;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.danger {
  border-radius: 999px;
  padding: 0.45rem 0.9rem;
  font-weight: 700;
  border: 1px solid rgba(196, 69, 69, 0.3);
  background: rgba(255, 255, 255, 0.85);
  color: #c44545;
  cursor: pointer;
}

.danger:hover {
  border-color: rgba(196, 69, 69, 0.55);
}

.history-verdict {
  grid-column: 1 / -1;
  color: rgba(16, 34, 68, 0.8);
}

.history-hint {
  grid-column: 1 / -1;
  color: rgba(16, 34, 68, 0.62);
  font-size: 0.9rem;
}

.history-empty {
  color: rgba(16, 34, 68, 0.7);
  padding: 1rem 0;
}

.history-more {
  display: flex;
  justify-content: center;
  padding-top: 0.25rem;
}

.detail {
  background: rgba(255, 255, 255, 0.9);
}

.detail-grid {
  display: grid;
  gap: 1.5rem;
  align-items: start;
}

.detail-media {
  display: grid;
  grid-template-columns: minmax(240px, 360px) minmax(0, 1fr);
  gap: 1.5rem;
  align-items: stretch;
}

.detail-image {
  min-height: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.image-note {
  color: rgba(16, 34, 68, 0.7);
  padding: 0.8rem 0;
}

.detail-img {
  max-width: 100%;
  max-height: 100%;
  width: 100%;
  border-radius: 1.2rem;
  box-shadow: 0 15px 40px rgba(23, 41, 79, 0.18);
  display: block;
  object-fit: contain;
}

.panel {
  border-radius: 1.5rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(248, 251, 255, 0.95);
  padding: 1.2rem;
  overflow: hidden;
}

.panel-title {
  font-weight: 700;
  color: #111b2e;
  margin: 0;
  list-style: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.panel-title::-webkit-details-marker {
  display: none;
}

.panel-title::after {
  content: '▾';
  color: rgba(16, 34, 68, 0.55);
  font-weight: 800;
}

details[open] > .panel-title::after {
  transform: rotate(180deg);
}

details > pre,
details > .analysis {
  margin-top: 0.8rem;
}

.detail pre {
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(15, 22, 40, 0.92);
  color: #e8f0ff;
  padding: 0.9rem;
  border-radius: 1rem;
  max-height: 320px;
  overflow-y: auto;
  font-size: 0.9rem;
}

.analysis {
  display: grid;
  gap: 1rem;
}

.analysis-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.75rem;
  border-radius: 999px;
  border: 1px solid rgba(111, 157, 236, 0.22);
  background: rgba(255, 255, 255, 0.85);
  color: rgba(16, 34, 68, 0.9);
  font-weight: 600;
  font-size: 0.9rem;
}

.analysis-sections {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
}

.analysis-section {
  border-radius: 1.2rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.82);
  padding: 1rem;
}

.analysis-section > summary {
  margin: 0;
  color: #111b2e;
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.analysis-section > summary::-webkit-details-marker {
  display: none;
}

.analysis-section > summary::after {
  content: '▾';
  color: rgba(16, 34, 68, 0.55);
  font-weight: 800;
}

.analysis-section[open] > summary::after {
  transform: rotate(180deg);
}

.analysis-section ul,
.analysis-section ol,
.analysis-section pre {
  margin-top: 0.8rem;
}

.analysis-section ul,
.analysis-section ol {
  margin: 0;
  padding-left: 1.2rem;
  color: rgba(16, 34, 68, 0.88);
}

.analysis-sub {
  margin-top: 0.35rem;
  color: rgba(16, 34, 68, 0.72);
}

.quiz {
  display: grid;
  gap: 0.8rem;
}

.quiz-error {
  color: #c44545;
  font-weight: 600;
}

.quiz-body {
  border-radius: 1.2rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  background: rgba(255, 255, 255, 0.82);
  padding: 1rem;
}

.quiz-question {
  font-weight: 700;
  color: #111b2e;
  margin-bottom: 0.75rem;
}

.quiz-options {
  display: grid;
  gap: 0.5rem;
}

.quiz-option {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  cursor: pointer;
  padding: 0.5rem 0.6rem;
  border-radius: 0.9rem;
  border: 1px solid rgba(17, 62, 125, 0.15);
  background: rgba(255, 255, 255, 0.85);
}

.quiz-option-text {
  flex: 1;
}

.quiz-explanation {
  margin-top: 0.6rem;
  color: rgba(16, 34, 68, 0.85);
}

.quiz-actions {
  margin-top: 0.75rem;
}

.quiz-result {
  margin-top: 0.9rem;
  padding: 0.9rem;
  border-radius: 1rem;
  border: 1px solid rgba(111, 157, 236, 0.18);
  background: rgba(248, 251, 255, 0.95);
  position: relative;
  overflow: visible;
}

.quiz-result[data-ok='true'] {
  border-color: rgba(10, 128, 96, 0.3);
}

.quiz-result[data-ok='false'] {
  border-color: rgba(196, 69, 69, 0.3);
}

.quiz-result-title {
  font-weight: 800;
  color: #111b2e;
}

.quiz-celebrate {
  margin: 0.65rem 0 0.6rem;
  padding: 0.9rem 1rem;
  border-radius: 1rem;
  border: 1px solid rgba(10, 128, 96, 0.26);
  background: rgba(255, 255, 255, 0.82);
  position: relative;
  z-index: 1;
}

.quiz-celebrate-title {
  font-weight: 900;
  color: rgba(16, 34, 68, 0.92);
}

.quiz-celebrate-sub {
  margin-top: 0.25rem;
  color: rgba(16, 34, 68, 0.75);
}

.quiz-confetti {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 999;
}

.confetti-piece {
  position: absolute;
  bottom: 12px;
  border-radius: 999px;
  opacity: 0;
  animation-name: confetti-pop;
  animation-timing-function: cubic-bezier(0.18, 0.8, 0.22, 1);
  animation-fill-mode: forwards;
}

@keyframes confetti-pop {
  0% {
    opacity: 0;
    transform: translate3d(0, 0, 0) rotate(0deg);
  }
  12% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: translate3d(var(--dx), var(--dy), 0) rotate(240deg);
  }
}

@media (max-width: 640px) {
  section {
    border-radius: 1.3rem;
  }

  .card-actions {
    flex-direction: column;
  }

  .history-item {
    grid-template-columns: 1fr;
    text-align: left;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }

  .detail-media {
    grid-template-columns: 1fr;
  }
}
</style>
