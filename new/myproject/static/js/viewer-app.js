// /var/www/jm_viewer/static/js/viewer-app.js (这是我们全新的独立脚本)

// 使用一个函数来包裹所有代码，避免污染全局作用域
function startViewer() {
    // 关键检查：轮询检查 Gsplat 库是否已经加载完成
    // 这是最稳妥的方式，无论 gsplat-bundle.js 是如何加载的
    const waitForGsplat = setInterval(() => {
        if (window.Gsplat && typeof window.Gsplat.WebGLRenderer === 'function') {
            clearInterval(waitForGsplat); // 找到了 Gsplat，停止轮询
            initializeApp(window.Gsplat);   // 开始执行我们的应用
        }
    }, 50); // 每 50 毫秒检查一次

    // 设置一个超时，以防 Gsplat 库加载失败
    setTimeout(() => {
        if (!window.Gsplat) {
            clearInterval(waitForGsplat);
            console.error("Gsplat library failed to load after 5 seconds.");
            const loadingText = document.getElementById('loading-text');
            if(loadingText) loadingText.textContent = "错误：核心3D库加载失败！";
        }
    }, 5000); // 5秒超时
}

// 我们的主应用逻辑函数
function initializeApp(Gsplat) {
    // 获取所有需要的HTML元素
    const canvas = document.createElement('canvas');
    const viewerContainer = document.getElementById('viewer-container');
    if (!viewerContainer) {
        console.error('Fatal Error: Element with id "viewer-container" not found.');
        return;
    }
    viewerContainer.appendChild(canvas);

    const loadingOverlay = document.getElementById('loading-overlay');
    const loadingText = document.getElementById('loading-text');
    const progressBarInner = document.getElementById('progress-bar-inner');
    const controlsHint = document.getElementById('controls-hint');
    const serverModelSelect = document.getElementById('server_model_select');
    const fileInput = document.getElementById('file_input');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebar = document.querySelector('.sidebar');

    if (!loadingOverlay || !controlsHint) {
        console.warn('Warning: Loading overlay or controls hint element is missing.');
    }

    let isFirstLoad = true;
    const renderer = new Gsplat.WebGLRenderer(canvas);
    const scene = new Gsplat.Scene();
    const camera = new Gsplat.Camera();
    const controls = new Gsplat.OrbitControls(camera, canvas);

    const loadModelFromURL = async (url) => {
        if (loadingOverlay) {
            loadingText.textContent = '正在加载模型...';
            progressBarInner.style.width = '0%';
            loadingOverlay.style.display = 'flex';
        }

        try {
            await Gsplat.Loader.LoadAsync(url, scene, (progress) => {
                if(progressBarInner) {
                    const percent = (progress * 100).toFixed(2);
                    progressBarInner.style.width = `${percent}%`;
                }
            });
            
            if (loadingOverlay) loadingOverlay.style.display = 'none';

            if (isFirstLoad && controlsHint) {
                isFirstLoad = false;
                controlsHint.classList.add('visible');
                setTimeout(() => controlsHint.classList.remove('visible'), 4000);
            }
        } catch (error) {
            console.error('Failed to load model:', error);
            if (loadingOverlay) {
                loadingText.textContent = '加载失败！请检查文件或网络。';
                if(progressBarInner) progressBarInner.style.backgroundColor = '#dc3545';
                setTimeout(() => {
                    loadingOverlay.style.display = 'none';
                    if(progressBarInner) progressBarInner.style.backgroundColor = '';
                }, 3000);
            }
        }
    };

    const animate = () => {
        controls.update();
        renderer.render(scene, camera);
        requestAnimationFrame(animate);
    };
    animate();

    if (sidebarToggle) sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('open'));
    if (serverModelSelect) serverModelSelect.addEventListener('change', (e) => loadModelFromURL(e.target.value));
    if (fileInput) fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) loadModelFromURL(URL.createObjectURL(file));
    });

    if (window.DEFAULT_MODEL_URL) {
        loadModelFromURL(window.DEFAULT_MODEL_URL);
    } else if (loadingOverlay) {
        loadingText.textContent = '请从侧边栏选择一个模型';
        loadingOverlay.style.display = 'flex';
    }
}

// 当整个页面（包括所有脚本）加载完毕后，开始我们的程序
window.addEventListener('load', startViewer);