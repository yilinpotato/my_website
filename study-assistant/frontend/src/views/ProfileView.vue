<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:3000'

const authStore = useAuthStore()
const router = useRouter()

const profile = reactive({
  username: '',
  display_name: '',
  age: '',
  role: 'student',
  courses: '',
  linked_email: '',
})

const passwordForm = reactive({
  current_password: '',
  new_password: '',
})

const message = ref('')
const error = ref('')
const passwordMessage = ref('')
const passwordError = ref('')

const pendingBindRequests = ref([])
const bindMessage = ref('')
const bindError = ref('')

const isAuthenticated = computed(() => authStore.isAuthenticated)
const linkedInfo = computed(() => authStore.user?.linked_user)

const loadProfile = async () => {
  if (!isAuthenticated.value) {
    router.push('/login')
    return
  }
  try {
    const data = await authStore.fetchProfile()
    profile.username = data.username || ''
    profile.display_name = data.display_name || ''
    profile.age = data.age ?? ''
    profile.role = data.role || 'student'
    profile.courses = (data.courses || []).join(', ')
    profile.linked_email = data.linked_user?.email || ''
  } catch (err) {
    error.value = err.message
  }
}

onMounted(loadProfile)

const loadBindRequests = async () => {
  bindMessage.value = ''
  bindError.value = ''
  if (!authStore.token) return

  try {
    const res = await fetch(`${API_BASE_URL}/api/bind/requests`, {
      headers: { Authorization: `Bearer ${authStore.token}` },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.message || '加载绑定请求失败')
    pendingBindRequests.value = Array.isArray(data?.items) ? data.items : []
  } catch (e) {
    bindError.value = e?.message || '加载绑定请求失败'
  }
}

onMounted(loadBindRequests)

const respondBindRequest = async (requestId, action) => {
  bindMessage.value = ''
  bindError.value = ''
  try {
    const res = await fetch(`${API_BASE_URL}/api/bind/requests/${requestId}/respond`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${authStore.token}`,
      },
      body: JSON.stringify({ action }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data?.message || '处理失败')

    bindMessage.value = action === 'approve' ? '已同意绑定' : '已拒绝绑定'
    await authStore.fetchProfile()
    await loadBindRequests()
  } catch (e) {
    bindError.value = e?.message || '处理失败'
  }
}

const handleSave = async () => {
  message.value = ''
  error.value = ''
  try {
    await authStore.updateProfile({
      username: profile.username,
      display_name: profile.display_name,
      age: profile.age ? Number(profile.age) : null,
      role: profile.role,
      courses: profile.courses
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
      linked_email: profile.linked_email || null,
    })
    message.value = '资料已保存'
  } catch (err) {
    error.value = err.message
  }
}

const handlePasswordChange = async () => {
  passwordMessage.value = ''
  passwordError.value = ''
  if (!passwordForm.current_password || !passwordForm.new_password) {
    passwordError.value = '请输入当前密码和新密码'
    return
  }
  try {
    await authStore.changePassword({ ...passwordForm })
    passwordMessage.value = '密码已更新，下次登录请使用新密码'
    passwordForm.current_password = ''
    passwordForm.new_password = ''
  } catch (err) {
    passwordError.value = err.message
  }
}
</script>

<template>
  <section class="profile-card">
    <header>
      <p class="eyebrow">Profile</p>
      <h1>个人信息</h1>
      <p>完善昵称、年龄、课程、身份角色，并可绑定家长/学生账号。</p>
    </header>

    <form @submit.prevent="handleSave">
      <label>
        账户名
        <input
          v-model="profile.username"
          type="text"
          minlength="3"
          maxlength="20"
          pattern="^[A-Za-z0-9_.\-]{3,20}$"
          required
        />
      </label>
      <label>
        昵称
        <input v-model="profile.display_name" type="text" placeholder="系统已为你生成，可自定义" />
      </label>
      <label>
        年龄
        <input v-model="profile.age" type="number" min="5" max="25" placeholder="如 16" />
      </label>
      <label>
        身份角色
        <select v-model="profile.role">
          <option value="student">学生</option>
          <option value="parent">家长</option>
        </select>
      </label>
      <label>
        选课（逗号分隔）
        <input v-model="profile.courses" type="text" placeholder="数学, 物理, 英语" />
      </label>
      <label>
        绑定邮箱（家长或学生）
        <input v-model="profile.linked_email" type="email" placeholder="ta 的登录邮箱" />
      </label>
      <button class="primary" type="submit">保存资料</button>
    </form>

    <p v-if="message" class="status success">{{ message }}</p>
    <p v-if="error" class="status error">{{ error }}</p>

    <div class="linked-info" v-if="linkedInfo">
      <p>已绑定：{{ linkedInfo.display_name }} ({{ linkedInfo.role }}) · {{ linkedInfo.email }}</p>
    </div>
  </section>

  <section class="profile-card" v-if="profile.role === 'student'">
    <header>
      <p class="eyebrow">Binding</p>
      <h2>绑定请求</h2>
      <p>如果家长通过昵称发起了绑定，你会在这里收到请求并选择同意或拒绝。</p>
    </header>

    <p v-if="bindMessage" class="status success">{{ bindMessage }}</p>
    <p v-if="bindError" class="status error">{{ bindError }}</p>

    <div v-if="pendingBindRequests.length" class="requests">
      <article v-for="r in pendingBindRequests" :key="r.id" class="request-item">
        <div>
          <p class="request-title">来自家长：{{ r.parent?.display_name }} · {{ r.parent?.email }}</p>
          <p class="request-meta">时间：{{ r.created_at }}</p>
        </div>
        <div class="request-actions">
          <button class="primary" type="button" @click="respondBindRequest(r.id, 'approve')">同意</button>
          <button class="ghost" type="button" @click="respondBindRequest(r.id, 'reject')">拒绝</button>
        </div>
      </article>
    </div>
    <p v-else class="muted">暂无待处理请求</p>
  </section>

  <section class="profile-card">
    <header>
      <p class="eyebrow">Security</p>
      <h2>修改密码</h2>
    </header>

    <form @submit.prevent="handlePasswordChange">
      <label>
        当前密码
        <input v-model="passwordForm.current_password" type="password" required />
      </label>
      <label>
        新密码
        <input v-model="passwordForm.new_password" type="password" minlength="8" required />
      </label>
      <button class="ghost" type="submit">更新密码</button>
    </form>

    <p v-if="passwordMessage" class="status success">{{ passwordMessage }}</p>
    <p v-if="passwordError" class="status error">{{ passwordError }}</p>
  </section>
</template>

<style scoped>
.profile-card {
  max-width: 720px;
  margin: 2rem auto;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}

h1,
h2 {
  color: #0f1d40;
}

form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

label {
  display: flex;
  flex-direction: column;
  font-weight: 600;
  color: #112347;
  gap: 0.4rem;
}

input,
select {
  border-radius: 999px;
  border: 1px solid rgba(17, 62, 125, 0.25);
  padding: 0.8rem 1.2rem;
  font-size: 1rem;
}

.primary {
  border: none;
  border-radius: 999px;
  padding: 0.9rem 1.8rem;
  font-weight: 600;
  background: linear-gradient(120deg, #6f9dec, #8bd4c8);
  color: #fff;
  cursor: pointer;
}

.ghost {
  border: 1px solid rgba(17, 62, 125, 0.25);
  background: rgba(255, 255, 255, 0.9);
  border-radius: 999px;
  padding: 0.7rem 1.4rem;
  cursor: pointer;
}

.status {
  font-weight: 600;
}

.status.success {
  color: #0a8060;
}

.status.error {
  color: #c44545;
}

.linked-info {
  font-weight: 600;
  color: #0b2a6b;
}

.requests {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

.request-item {
  border-radius: 1.2rem;
  border: 1px solid rgba(111, 157, 236, 0.16);
  padding: 1rem;
  background: rgba(248, 251, 255, 0.95);
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: center;
}

.request-title {
  font-weight: 700;
  color: #0f1d40;
}

.request-meta {
  color: rgba(16, 34, 68, 0.72);
  margin-top: 0.25rem;
}

.request-actions {
  display: flex;
  gap: 0.6rem;
}

.muted {
  color: rgba(16, 34, 68, 0.72);
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}
</style>
