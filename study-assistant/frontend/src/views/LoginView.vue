<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()

const email = ref('')
const password = ref('')
const message = ref('')
const error = ref('')
const resetRequested = ref(false)
const guestLoading = ref(false)
const handleGuest = async () => {
  guestLoading.value = true
  error.value = ''
  try {
    await authStore.guestLogin()
    await router.push('/note-assistant')
  } catch (err) { error.value = err.message }
  finally { guestLoading.value = false }
}

const handleLogin = async () => {
  message.value = ''
  error.value = ''
  try {
    await authStore.login({ email: email.value, password: password.value })
    message.value = '登录成功，正在跳转...'
    setTimeout(() => router.push('/dashboard'), 600)
  } catch (err) {
    error.value = err.message
  }
}

const handleResetRequest = async () => {
  error.value = ''
  if (!email.value) {
    error.value = '请输入注册邮箱以接收验证码'
    return
  }
  try {
    await authStore.requestPasswordReset(email.value)
    resetRequested.value = true
    message.value = '重置验证码已发送至邮箱，请查收。'
  } catch (err) {
    error.value = err.message
  }
}
</script>

<template>
  <section class="auth-card">
    <header>
      <p class="eyebrow">Welcome back</p>
      <h1>登录 AI 学习助手</h1>
      <p>创建独立的访客空间，体验笔记、思维导图与错题分析。</p>
    </header>

    <button class="primary" type="button" :disabled="guestLoading" @click="handleGuest">{{ guestLoading ? '正在进入…' : '开始访客体验' }}</button>
    <details><summary>已有账号登录</summary>
    <form @submit.prevent="handleLogin">
      <label>
        邮箱
        <input v-model="email" type="email" placeholder="name@example.com" required />
      </label>
      <label>
        密码
        <input v-model="password" type="password" placeholder="请输入密码" required />
      </label>
      <button class="primary" type="submit">立即登录</button>
    </form></details>



    <p v-if="message" class="status success">{{ message }}</p>
    <p v-if="error" class="status error">{{ error }}</p>
    <p v-if="resetRequested" class="note">请在 10 分钟内使用验证码完成重置。</p>
  </section>
</template>

<style scoped>
.auth-card {
  max-width: 520px;
  margin: 2rem auto;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 2rem;
  padding: clamp(1.5rem, 4vw, 3rem);
  box-shadow: 0 25px 60px rgba(21, 47, 89, 0.08);
  display: flex;
  flex-direction: column;
  gap: 1.2rem;
}

h1 {
  font-size: 2rem;
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

input {
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

.actions {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.actions a {
  color: #0b2a6b;
}

.ghost {
  border: 1px solid rgba(17, 62, 125, 0.25);
  background: rgba(255, 255, 255, 0.9);
  border-radius: 999px;
  padding: 0.7rem 1.2rem;
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

.note {
  color: rgba(15, 34, 70, 0.6);
}

.eyebrow {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: rgba(29, 60, 120, 0.7);
  font-size: 0.82rem;
  margin-bottom: 0.4rem;
}
</style>
