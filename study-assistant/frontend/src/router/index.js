import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/about',
      name: 'about',
      // route level code-splitting
      // this generates a separate chunk (About.[hash].js) for this route
      // which is lazy-loaded when the route is visited.
      component: () => import('../views/AboutView.vue'),
    },
    {
      path: '/error-book',
      name: 'error-book',
      component: () => import('../views/ErrorBookView.vue'),
    },
    {
      path: '/note-assistant',
      name: 'note-assistant',
      component: () => import('../views/NoteAssistantView.vue'),
    },
    {
      path: '/mind-map',
      name: 'mind-map',
      component: () => import('../views/MindMapView.vue'),
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      component: () => import('../views/LearningDashboardView.vue'),
    },
    {
      path: '/parent',
      name: 'parent',
      component: () => import('../views/ParentView.vue'),
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
    },
    {
      path: '/register',
      name: 'register',
      redirect: '/login',
      component: () => import('../views/RegisterView.vue'),
    },
    {
      path: '/verify',
      name: 'verify',
      redirect: '/login',
      component: () => import('../views/VerifyEmailView.vue'),
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('../views/ProfileView.vue'),
    },
  ],
})

export default router
