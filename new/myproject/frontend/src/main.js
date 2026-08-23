import * as SPLAT from 'gsplat';

document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('viewer-container');
    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText = document.getElementById('loading-text');
    const progressBarInner = document.getElementById('progress-bar-inner');
    const fileInput = document.getElementById('file_input');
    const serverModelSelect = document.getElementById('server_model_select');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');
    
    if (!container) return;

    let renderer, scene, camera, controls, animationFrameId;

    const frame = () => {
        if (controls) controls.update();
        if (renderer && scene && camera) renderer.render(scene, camera);
        animationFrameId = requestAnimationFrame(frame);
    };

    async function loadModel(url) {
        if (!url) return;

        if (animationFrameId) cancelAnimationFrame(animationFrameId);
        if (controls) controls.dispose();

        if (loadingOverlay) {
            loadingOverlay.style.display = 'flex';
            loadingText.innerText = '正在加载模型...';
            progressBarInner.style.width = '0%';
        }

        try {
            if (!renderer) {
                renderer = new SPLAT.WebGLRenderer();
                container.innerHTML = '';
                container.appendChild(renderer.canvas);
            }
            
            const newScene = new SPLAT.Scene();
            const newCamera = new SPLAT.Camera();
            
            await SPLAT.Loader.LoadAsync(url, newScene, (progress) => {
                if (progressBarInner) {
                    const percentage = (progress * 100).toFixed(2);
                    progressBarInner.style.width = `${percentage}%`;
                    loadingText.innerText = `加载中... ${percentage}%`;
                }
            });

            const newControls = new SPLAT.OrbitControls(newCamera, renderer.canvas);

            scene = newScene;
            camera = newCamera;
            controls = newControls;

            frame();

        } catch (e) {
            console.error("模型加载失败:", e);
            alert(`模型加载失败: ${e.message}`);
        } finally {
            if (loadingOverlay) loadingOverlay.style.display = 'none';
        }
    }

    // Sidebar toggle
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            sidebar.classList.toggle('open');
        });
        document.addEventListener('click', (e) => {
            if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && e.target !== sidebarToggle) {
                sidebar.classList.remove('open');
            }
        });
    }
    
    // Local file
    if (fileInput) {
        fileInput.addEventListener('change', (event) => {
            const file = event.target.files[0];
            if (file) {
                if (serverModelSelect) serverModelSelect.value = "";
                const url = URL.createObjectURL(file);
                loadModel(url);
            }
        });
    }

    // Server select
    if (serverModelSelect) {
        serverModelSelect.addEventListener('change', () => {
            const url = serverModelSelect.value;
            if (url) {
                if (fileInput) fileInput.value = "";
                loadModel(url);
            }
        });
    }
    
    // Default model
    if (window.DEFAULT_MODEL_URL) {
        loadModel(window.DEFAULT_MODEL_URL);
    }
});
