import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.158.0/build/three.module.js';
import * as GaussianSplats3D from 'https://cdn.jsdelivr.net/npm/@mkkellogg/gaussian-splats-3d@0.4.7/build/gaussian-splats-3d.module.js';

window.__SPLAT_DEMO_MODULE_LOADED__ = true;

function formatLocalDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function safeGetLocalStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetLocalStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const card = document.getElementById('daily-splat-card');
  const dismissBtn = document.getElementById('dismiss-splat-demo');
  const root = document.getElementById('splat-viewer');
  const fallback = document.getElementById('splat-demo-fallback');

  if (!card || !dismissBtn || !root) return;

  const ctx = window.TRAINING_CONTEXT || {};
  const username = typeof ctx.username === 'string' ? ctx.username : 'anonymous';
  const todayKey = formatLocalDateKey(new Date());
  const storageKey = `training.splatDemo.lastShown:${username}`;
  const forceShow = Boolean(ctx.forceShowSplatDemo);

  let viewer = null;
  let renderer = null;
  let rafId = null;
  let active = true;
  let initialized = false;

  const overlay = document.createElement('div');
  overlay.style.cssText = [
    'position:absolute',
    'inset:0',
    'display:flex',
    'align-items:center',
    'justify-content:center',
    'color:#fff',
    'font-size:14px',
    'background:rgba(0,0,0,0.35)',
    'backdrop-filter: blur(2px)',
    'z-index:2'
  ].join(';');
  overlay.textContent = '正在加载 3D 示例...';

  const stop = () => {
    active = false;
    initialized = false;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
    if (viewer && typeof viewer.dispose === 'function') {
      try { viewer.dispose(); } catch { /* ignore */ }
    }
    viewer = null;
    if (renderer) {
      try { renderer.dispose(); } catch { /* ignore */ }
    }
    renderer = null;
    root.innerHTML = '';
    window.__SPLAT_DEMO_READY__ = false;
  };

  dismissBtn.addEventListener('click', () => {
    stop();
    card.style.display = 'none';
  });

  const init = async () => {
    try {
      if (initialized) return;
      initialized = true;

      // 若不是强制展示，则按“每日首次”规则决定是否展示
      if (!forceShow) {
        const lastShown = safeGetLocalStorage(storageKey);
        if (lastShown === todayKey) {
          card.style.display = 'none';
          initialized = false;
          return;
        }
        safeSetLocalStorage(storageKey, todayKey);
      }

      card.style.display = '';
      if (fallback) {
        fallback.style.display = '';
        fallback.textContent = '正在加载 3D 示例...';
      }

      // 等待布局稳定，确保拿到尺寸
      await new Promise((r) => requestAnimationFrame(() => r()));

      const rect = root.getBoundingClientRect();
      const width = Math.max(1, Math.floor(rect.width || 640));
      const height = Math.max(1, Math.floor(rect.height || 260));

      root.style.position = 'relative';
      root.appendChild(overlay);

      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(width, height, false);
      root.appendChild(renderer.domElement);

      const camera = new THREE.PerspectiveCamera(65, width / height, 0.1, 500);
      camera.position.copy(new THREE.Vector3().fromArray([-1, -4, 6]));
      camera.up = new THREE.Vector3().fromArray([0, -1, -0.6]).normalize();
      camera.lookAt(new THREE.Vector3().fromArray([0, 4, 0]));

      viewer = new GaussianSplats3D.Viewer({
        selfDrivenMode: false,
        renderer,
        camera,
        useBuiltInControls: true,
        ignoreDevicePixelRatio: false,
        // 避免 COOP/COEP 需求（SharedArrayBuffer）
        sharedMemoryForWorkers: false,
        gpuAcceleratedSort: false,
        logLevel: GaussianSplats3D.LogLevel.None,
        sphericalHarmonicsDegree: 0,
      });

      const splatPath = (ctx && typeof ctx.splatDemoPath === 'string' && ctx.splatDemoPath) ? ctx.splatDemoPath : '/static/models/example.splat';

      await viewer.addSplatScene(splatPath, {
        showLoadingUI: true,
        splatAlphaRemovalThreshold: 5,
      });

      if (fallback) fallback.style.display = 'none';
      if (overlay && overlay.parentElement) overlay.parentElement.removeChild(overlay);
      window.__SPLAT_DEMO_READY__ = true;

      const resize = () => {
        if (!renderer || !viewer) return;
        const r = root.getBoundingClientRect();
        const w = Math.max(1, Math.floor(r.width || 640));
        const h = Math.max(1, Math.floor(r.height || 260));
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };

      window.addEventListener('resize', resize);

      const loop = () => {
        if (!active || !viewer) return;
        viewer.update();
        viewer.render();
        rafId = requestAnimationFrame(loop);
      };

      loop();
    } catch (err) {
      console.warn('splat demo init failed:', err);
      if (fallback) {
        fallback.style.display = '';
        fallback.textContent = '3D 示例加载失败（请打开控制台查看 splat demo 报错）。';
      }
      // 失败也不要阻塞训练页
      stop();
    }
  };

  // 训练开始时再初始化（符合产品预期）
  document.addEventListener('training:started', () => {
    init();
  });
});
