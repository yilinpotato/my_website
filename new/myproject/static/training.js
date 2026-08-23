//training.js
document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const videoElement = document.getElementById('webcam');
    const canvasElement = document.getElementById('output_canvas');
    const canvasCtx = canvasElement.getContext('2d');
    const startStopBtn = document.getElementById('startStopBtn');
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');
    const performanceSelect = document.getElementById('performance-select');
    const currentActionElem = document.getElementById('current-action');
    const actionScoreElem = document.getElementById('action-score');
    const repCountElem = document.getElementById('rep-count');
    const hardwareDot = document.getElementById('hardware-dot');
    const hardwareStatusElem = document.getElementById('hardware-status');
    const hardwareDetailElem = document.getElementById('hardware-detail');
    const currentFeedbackContainer = document.getElementById('current-feedback-container');
    const currentFeedbackIcon = document.getElementById('current-feedback-icon');
    const currentFeedbackText = document.getElementById('current-feedback-text');
    const feedbackLogList = document.getElementById('feedback-log-list');
    
    // ★★★ 新增 DOM 元素 ★★★
    const calorieCountElem = document.getElementById('calorie-count'); // 注意：这个元素在HTML中可能已经被移除了，需要检查
    const actionSelect = document.getElementById('action-select');
    
    const trainingContext = window.TRAINING_CONTEXT || {};

    // ★★★ 新增：自动/手动模式切换 ★★★
    const autoModeIndicator = document.getElementById('auto-mode-indicator');
    const manualModeSelector = document.getElementById('manual-mode-selector');
    const toggleModeBtn = document.getElementById('toggle-mode-btn');
    const detectedActionText = document.getElementById('detected-action-text');
    let isAutoMode = true; // 默认为自动模式

    // ★★★ 新增：常量定义 ★★★
    const METS = {
        'squat': 5.0,
        'pushup': 8.0,
        'jumping_jack': 8.0,
        'plank': 3.5,
        'lunge': 4.0
    };

    const MUSCLE_COLORS = {
        target: '#0d6efd',
        issue: '#dc3545'
    };

    let latestMusclePayload = null;
    let muscleRequestSeq = 0;

    const getHighlighterInstance = () => (window.MuscleHighlighters && window.MuscleHighlighters[0]) || null;

    function resolveDefaultActionKey(name) {
        if (!name) return null;
        const normalized = name.trim().toLowerCase();
        if (DEFAULT_ACTION_MUSCLES[normalized]) {
            return normalized;
        }
        return ACTION_ALIAS_MAP[name] || ACTION_ALIAS_MAP[normalized] || null;
    }

    function refreshMuscleUsage(actionName) {
        if (!actionName) return;
        const requestId = ++muscleRequestSeq;
        const runFetch = () => requestMuscleUsage(actionName, requestId);

        if (getHighlighterInstance()) {
            runFetch();
            return;
        }

        const handler = () => {
            document.removeEventListener('muscleHighlighter:ready', handler);
            runFetch();
        };
        document.addEventListener('muscleHighlighter:ready', handler, { once: true });
    }

    async function requestMuscleUsage(actionName, requestId) {
        try {
            const params = new URLSearchParams();
            if (actionName) {
                params.set('action', actionName);
            }
            const query = params.toString();
            const response = await fetch(`/api/muscle_usage${query ? `?${query}` : ''}`);
            if (!response.ok) throw new Error('无法获取肌群数据');
            const payload = await response.json();
            if (requestId !== muscleRequestSeq) return;
            applyMusclePayload(payload);
        } catch (error) {
            console.warn('肌群数据接口异常，使用默认映射', error);
            if (requestId !== muscleRequestSeq) return;
            const fallbackKey = resolveDefaultActionKey(actionName);
            applyMusclePayload({
                targets: fallbackKey ? DEFAULT_ACTION_MUSCLES[fallbackKey] : [],
                issues: []
            });
        }
    }

    function applyMusclePayload(payload) {
        latestMusclePayload = payload;
        const highlighter = getHighlighterInstance();
        if (!highlighter) return;

        const layers = [];
        if (Array.isArray(payload.targets) && payload.targets.length) {
            layers.push({ slugs: payload.targets, color: MUSCLE_COLORS.target, priority: 1 });
        }
        if (Array.isArray(payload.issues) && payload.issues.length) {
            layers.push({ slugs: payload.issues, color: MUSCLE_COLORS.issue, priority: 2 });
        }

        if (typeof highlighter.setLayers === 'function') {
            highlighter.setLayers(layers);
        } else if (layers[0]) {
            highlighter.highlight(layers[0].slugs, layers[0].color);
        }
    }

    const DEFAULT_ACTION_MUSCLES = {
        squat: ['quadriceps', 'hamstring', 'gluteal', 'calves', 'tibialis'],
        pushup: ['chest', 'triceps', 'deltoids', 'biceps', 'abs'],
        jumping_jack: ['deltoids', 'quadriceps', 'calves', 'trapezius', 'upper-back'],
        plank: ['abs', 'obliques', 'gluteal', 'deltoids', 'trapezius'],
        lunge: ['quadriceps', 'hamstring', 'gluteal', 'calves']
    };

    const ACTION_ALIAS_MAP = {
        '深蹲': 'squat',
        '俯卧撑': 'pushup',
        '开合跳': 'jumping_jack',
        '平板支撑': 'plank',
        '平板': 'plank',
        '弓步蹲': 'lunge',
        '弓步': 'lunge',
        'jumping jack': 'jumping_jack',
        'jumping-jack': 'jumping_jack',
        'push-up': 'pushup',
        'lunges': 'lunge'
    };

    // 性能预设配置
    const performanceSettings = {
        low: { modelComplexity: 0, resolution: { width: 640, height: 480 }, processInterval: 100 },
        medium: { modelComplexity: 1, resolution: { width: 640, height: 480 }, processInterval: 66 },
        high: { modelComplexity: 1, resolution: { width: 1280, height: 720 }, processInterval: 40 }
    };
    let currentPerformanceTier = 'medium';

    // --- State Management ---
    let isCameraOn = false;
    let camera;
    let pose;
    let lastSentTime = 0;
    // ★ 修改 ★ WebSocket处理速度快，可以适当缩短发送间隔
    const sendInterval = 100; // 从 500ms 缩短到 100ms
    let lastFrameProcessTime = 0;

    let sessionData = { startTime: null, reps: 0, scores: [] };
    let feedbackHistory = [];
    const MAX_LOG_ITEMS = 10;
    let noPoseDetectedCounter = 0;
    
    // ★★★ 新增：状态变量 ★★★
    let caloriesBurned = 0;
    let currentAction = 'squat'; // 默认动作
    let lastCalorieUpdate = 0;
    let sessionFramesBuffer = [];
    let sessionActionSet = new Set();
    const MAX_SESSION_FRAMES = 4500;
    // 尝试从页面获取用户体重，如果没有则使用默认值
    // 注意：这里假设我们在模板中把用户体重渲染到了某个隐藏字段或JS变量中，暂时先用默认值
    const USER_WEIGHT_KG = Number(trainingContext.userWeight) || 70; 
    const LANDMARKS_PER_FRAME = 33;
    const LANDMARK_DECIMALS = 5;

    function calculateCalories(deltaSeconds) {
        if (!deltaSeconds || deltaSeconds <= 0) return 0;
        const mets = METS[currentAction] || 3.5;
        // Calorie formula: (METS * 3.5 * weight) / 200 per minute
        const caloriesPerMinute = (mets * 3.5 * USER_WEIGHT_KG) / 200;
        return caloriesPerMinute * (deltaSeconds / 60);
    }

    function applyActionChange(nextAction, options = {}) {
        if (!nextAction) return;
        const { source = 'system', force = false } = options;
        const canonical = resolveDefaultActionKey(nextAction) || nextAction;
        if (!force && canonical === currentAction) {
            return;
        }

        currentAction = canonical;
        if (source !== 'select' && actionSelect) {
            const match = Array.from(actionSelect.options || []).some(option => option.value === canonical);
            if (match && actionSelect.value !== canonical) {
                actionSelect.value = canonical;
            }
        }

        refreshMuscleUsage(canonical);
    }

    // --- WebSocket 相关代码 ---
    // ★ 新增 ★
let websocket = null;
const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';


// ★★★ 核心修改：直接连接到后端开放的 14427 端口 ★★★
const WEBSOCKET_URL = `${wsProtocol}://${window.location.hostname}:14427`;
    let transportMode = 'websocket';
    let wsConnectTimeout = null;
    let httpRequestInFlight = false;

    // --- 硬件 WebSocket 预留接口 ---
    // 默认端口 14428，可通过 window.TRAINING_CONTEXT.hardwareWsUrl 覆盖
    const HARDWARE_WEBSOCKET_URL = (trainingContext && trainingContext.hardwareWsUrl)
        ? String(trainingContext.hardwareWsUrl)
        : `${wsProtocol}://${window.location.hostname}:14428`;
    let hardwareSocket = null;
    let hardwareReconnectDelayMs = 500;
    let hardwareReconnectTimer = null;

    function setHardwareUiStatus(connected, detailText) {
        if (hardwareDot) {
            hardwareDot.classList.toggle('active', Boolean(connected));
        }
        if (hardwareStatusElem) {
            hardwareStatusElem.textContent = connected ? '已连接' : '未连接';
        }
        if (hardwareDetailElem) {
            const suffix = detailText ? ` | ${detailText}` : '';
            hardwareDetailElem.textContent = `WS: ${HARDWARE_WEBSOCKET_URL}${suffix}`;
        }
    }

    function clearHardwareReconnectTimer() {
        if (hardwareReconnectTimer) {
            clearTimeout(hardwareReconnectTimer);
            hardwareReconnectTimer = null;
        }
    }

    function scheduleHardwareReconnect() {
        clearHardwareReconnectTimer();
        const delay = hardwareReconnectDelayMs;
        hardwareReconnectDelayMs = Math.min(hardwareReconnectDelayMs * 1.6, 8000);
        hardwareReconnectTimer = setTimeout(() => {
            connectHardwareWebSocket();
        }, delay);
    }

    function connectHardwareWebSocket() {
        try {
            if (!hardwareDot && !hardwareStatusElem && !hardwareDetailElem) return;

            clearHardwareReconnectTimer();
            if (hardwareSocket) {
                try { hardwareSocket.close(); } catch { /* ignore */ }
                hardwareSocket = null;
            }

            setHardwareUiStatus(false, '连接中');
            hardwareSocket = new WebSocket(HARDWARE_WEBSOCKET_URL);

            hardwareSocket.addEventListener('open', () => {
                hardwareReconnectDelayMs = 500;
                setHardwareUiStatus(true, 'online');
            });

            hardwareSocket.addEventListener('message', (event) => {
                // 预留协议：建议发送 JSON，例如 {"connected":true,"device":"xxx","battery":80}
                let payload = null;
                try {
                    payload = JSON.parse(event.data);
                } catch {
                    // 非 JSON 信息也允许透传展示
                    setHardwareUiStatus(true, String(event.data).slice(0, 60));
                    return;
                }

                if (payload && typeof payload === 'object') {
                    const connected = payload.connected !== undefined ? Boolean(payload.connected) : true;
                    const device = payload.device ? String(payload.device) : '';
                    const battery = (payload.battery !== undefined && payload.battery !== null) ? `${payload.battery}%` : '';
                    const detail = [device, battery].filter(Boolean).join(' / ');
                    setHardwareUiStatus(connected, detail || 'online');
                }
            });

            hardwareSocket.addEventListener('close', () => {
                setHardwareUiStatus(false, '离线');
                scheduleHardwareReconnect();
            });

            hardwareSocket.addEventListener('error', () => {
                setHardwareUiStatus(false, '异常');
                // error 之后往往紧跟 close，这里也安排重连以提高稳健性
                scheduleHardwareReconnect();
            });
        } catch (err) {
            console.warn('硬件 WS 初始化失败:', err);
            setHardwareUiStatus(false, '不可用');
        }
    }

    function processServerPayload(data) {
        if (!data || typeof data !== 'object') {
            addFeedbackMessage('服务器返回了未知数据格式', 'error');
            return;
        }

        updateDashboard({
            score: data.score,
            action: data.action,
            reps: data.reps
        });

        if (isCameraOn && data.score > 0) {
            sessionData.scores.push(data.score);
        }
        if (isCameraOn && data.reps !== undefined) {
            sessionData.reps = data.reps;
        }

        if (Array.isArray(data.errors) && data.errors.length > 0) {
            const errorMessage = String(data.errors[0]).split(': ')[1] || data.errors[0];
            addFeedbackMessage(errorMessage, 'warning');
        } else if (typeof data.score === 'number' && data.score > 95) {
            addFeedbackMessage('动作标准，继续保持！', 'ok');
        }
    }

    // ★ 新增 ★ 用于处理从服务器收到的 WebSocket 消息
    function handleServerMessage(event) {
        try {
            const data = JSON.parse(event.data);
            processServerPayload(data);
        } catch (error) {
            console.error('解析服务器消息失败:', error);
            addFeedbackMessage('无法解析服务器响应', 'error');
        }
    }
    // --- WebSocket 相关代码结束 ---

    function clearWebsocketConnectTimer() {
        if (wsConnectTimeout) {
            clearTimeout(wsConnectTimeout);
            wsConnectTimeout = null;
        }
    }

    function switchToHttpTransport(reason = '') {
        if (transportMode === 'http') return;
        transportMode = 'http';
        clearWebsocketConnectTimer();
        if (websocket) {
            try {
                websocket.onopen = null;
                websocket.onmessage = null;
                websocket.onclose = null;
                websocket.onerror = null;
                websocket.close();
            } catch (socketError) {
                console.warn('关闭 WebSocket 时发生异常', socketError);
            }
            websocket = null;
        }
        if (reason) {
            addFeedbackMessage(reason, 'warning');
        }
    }

    async function sendPoseDataViaHttp(payload) {
        if (httpRequestInFlight) return;
        httpRequestInFlight = true;
        try {
            const resp = await fetch('/api/process_pose', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                credentials: 'same-origin'
            });
            if (!resp.ok) {
                throw new Error('HTTP ' + resp.status);
            }
            const data = await resp.json();
            processServerPayload(data);
        } catch (error) {
            console.error('HTTP 评估失败:', error);
        } finally {
            httpRequestInFlight = false;
        }
    }


    // --- UI Update Functions ---
    function updateDashboard(data) {
        // 如果数据中没有action和reps，则不更新它们，维持现有值
        if (data.action) {
            currentActionElem.textContent = data.action;
            applyActionChange(data.action, { source: 'server' });
        }
        if (data.score !== undefined) {
            const numericScore = Math.round(Number(data.score) || 0);

            // 无有效信息时显示“--”
            if (!Number.isFinite(numericScore) || numericScore <= 0) {
                actionScoreElem.textContent = '--';
                actionScoreElem.classList.remove('grade-a', 'grade-b', 'grade-c');
                return;
            }

            // ABC 映射：A>=90, B>=75, C<75（可按需调整）
            let grade = 'C';
            if (numericScore >= 90) grade = 'A';
            else if (numericScore >= 75) grade = 'B';

            actionScoreElem.textContent = grade;
            actionScoreElem.classList.remove('grade-a', 'grade-b', 'grade-c');
            actionScoreElem.classList.add(grade === 'A' ? 'grade-a' : grade === 'B' ? 'grade-b' : 'grade-c');
        }
        if (data.reps !== undefined) repCountElem.textContent = data.reps;
    }

    // 页面加载后立即尝试连接硬件 WS（不影响训练主流程）
    connectHardwareWebSocket();
    
    // ★★★ 新增：事件监听 ★★★
    // 切换自动/手动模式
    if (toggleModeBtn) {
        toggleModeBtn.addEventListener('click', () => {
            isAutoMode = !isAutoMode;
            if (isAutoMode) {
                autoModeIndicator.style.display = 'flex';
                manualModeSelector.style.display = 'none';
                toggleModeBtn.textContent = '切换手动';
                addFeedbackMessage("已切换至 AI 自动识别模式", "info");
            } else {
                autoModeIndicator.style.display = 'none';
                manualModeSelector.style.display = 'block';
                toggleModeBtn.textContent = '切换自动';
                addFeedbackMessage("已切换至手动选择模式", "info");
                // 切换到手动时，立即应用当前选择的动作
                const manualAction = actionSelect ? actionSelect.value : currentAction;
                applyActionChange(manualAction, { source: 'select', force: true });
            }
        });
    }

    if (actionSelect) {
        actionSelect.addEventListener('change', (e) => {
            if (!isAutoMode) {
                applyActionChange(e.target.value, { source: 'select', force: true });
            }
        });
        // 初始化
        applyActionChange(actionSelect.value, { source: 'init', force: true });
    } else {
        refreshMuscleUsage(currentAction);
    }

    // training.html 中的俯卧撑分析入口已移动至历史页面

    // ★★★ 新增：模拟深度学习动作识别 (Placeholder) ★★★
    function detectActionFromLandmarks(landmarks) {
        // 这里应该是调用深度学习模型的地方
        // 目前我们只是做一个简单的模拟或者返回 null 让它保持等待状态
        // 或者根据简单的几何特征做猜测
        
        // 示例：简单规则猜测 (仅作演示)
        // 如果手腕高于头部 -> Jumping Jack?
        // 如果髋部很低 -> Squat?
        
        // 暂时返回 null，表示"正在识别..." 或者随机返回一个动作来演示UI变化
        // return 'squat'; 
        return null; 
    }

    function addFeedbackMessage(text, type = 'info') {
        // 优化：如果最新的反馈与当前相同，则不重复添加
        if (currentFeedbackText.textContent === text) return;

        const now = new Date();
        const timestamp = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        currentFeedbackText.textContent = text;
        currentFeedbackContainer.className = `status-${type}`;

        if (type === 'error') {
            currentFeedbackIcon.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`;
        } else if (type === 'warning') {
            currentFeedbackIcon.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
        } else { // 'ok' 和 'info'
            currentFeedbackIcon.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`;
        }

        const logEntry = { text, type, timestamp };
        feedbackHistory.unshift(logEntry);

        const li = document.createElement('li');
        li.innerHTML = `<span class="timestamp">[${timestamp}]</span> <span class="log-text">${text}</span>`;
        feedbackLogList.prepend(li);

        if (feedbackLogList.children.length > MAX_LOG_ITEMS) {
            feedbackLogList.lastElementChild.remove();
            feedbackHistory.pop();
        }
    }

    function checkPoseValidity(landmarks) {
        if (!landmarks) return false;
        // 检查后端需要的关键点
        const requiredLandmarks = [11, 12, 13, 14, 15, 16, 23, 24]; // 肩、肘、腕、髋
        for (const index of requiredLandmarks) {
            const landmark = landmarks[index];
            if (!landmark || landmark.visibility < 0.6) {
                return false;
            }
        }
        return true;
    }

    function buildLandmarkFrame(landmarks) {
        const frame = [];
        for (let i = 0; i < LANDMARKS_PER_FRAME; i++) {
            const lm = landmarks[i] || {};
            frame.push([
                Number((lm.x ?? 0).toFixed(LANDMARK_DECIMALS)),
                Number((lm.y ?? 0).toFixed(LANDMARK_DECIMALS)),
                Number((lm.z ?? 0).toFixed(LANDMARK_DECIMALS))
            ]);
        }
        return frame;
    }

    function recordSessionFrame(landmarks) {
        if (!Array.isArray(landmarks)) return;
        const activeAction = currentAction || 'unknown';
        sessionActionSet.add(activeAction);
        sessionFramesBuffer.push({
            action: activeAction,
            landmarks: buildLandmarkFrame(landmarks)
        });
        if (sessionFramesBuffer.length > MAX_SESSION_FRAMES) {
            sessionFramesBuffer.shift();
        }
    }

    function resetSessionFrames() {
        sessionFramesBuffer = [];
        sessionActionSet = new Set();
    }

    function getSessionSummary() {
        const duration = sessionData.startTime ? Math.round((Date.now() - sessionData.startTime) / 1000) : 0;
        const repCount = parseInt(repCountElem.textContent, 10) || 0;
        const avgScore = sessionData.scores.length
            ? sessionData.scores.reduce((a, b) => a + b, 0) / sessionData.scores.length
            : 0;
        return { duration, repCount, avgScore: Number(avgScore.toFixed(2)) };
    }

    async function exportSessionFrames() {
        if (!sessionFramesBuffer.length) {
            sessionActionSet.clear();
            return;
        }
        const grouped = sessionFramesBuffer.reduce((acc, entry) => {
            if (!entry || !entry.landmarks) {
                return acc;
            }
            const key = entry.action || 'unknown';
            if (!acc[key]) acc[key] = [];
            acc[key].push(entry.landmarks);
            return acc;
        }, {});

        const actions = Object.keys(grouped);
        if (!actions.length) {
            resetSessionFrames();
            return;
        }

        const { duration, repCount } = getSessionSummary();
        const successes = [];
        const failures = [];

        for (const actionName of actions) {
            const frames = grouped[actionName];
            if (!frames || !frames.length) continue;
            try {
                const result = await sendFramesForAction(actionName, frames, duration, repCount);
                successes.push(`${result.action_name} (${result.filename})`);
            } catch (error) {
                console.error('Export frames failed:', error);
                failures.push(`${actionName}: ${error.message || '未知错误'}`);
            }
        }

        resetSessionFrames();

        if (successes.length) {
            addFeedbackMessage(`动作数据已导出：${successes.join('、')}`, 'ok');
        }
        if (failures.length) {
            addFeedbackMessage(`部分动作导出失败：${failures.join('；')}`, 'warning');
        }
    }

    async function sendFramesForAction(actionName, frames, durationSeconds, reps) {
        const payload = {
            action_name: actionName || 'unknown',
            frames,
            duration_seconds: durationSeconds,
            reps
        };
        const response = await fetch('/api/training/export_frames', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || '动作点数据导出失败');
        }
        return data;
    }

    // --- MediaPipe Pose processing logic ---
    function onResults(results) {
        if (canvasElement.width !== videoElement.videoWidth) {
            canvasElement.width = videoElement.videoWidth;
            canvasElement.height = videoElement.videoHeight;
        }

        canvasCtx.save();
        canvasCtx.clearRect(0, 0, canvasElement.width, canvasElement.height);
        canvasCtx.translate(canvasElement.width, 0);
        canvasCtx.scale(-1, 1);
        canvasCtx.drawImage(results.image, 0, 0, canvasElement.width, canvasElement.height);

        if (results.poseLandmarks) {
            noPoseDetectedCounter = 0;
            window.drawConnectors(canvasCtx, results.poseLandmarks, window.POSE_CONNECTIONS, { color: '#00FF00', lineWidth: 4 });
            window.drawLandmarks(canvasCtx, results.poseLandmarks, { color: '#FF0000', lineWidth: 2, radius: 4 });
            
            const isPoseValid = checkPoseValidity(results.poseLandmarks);
            if (isPoseValid) {
                const now = Date.now();
                
                // ★★★ 新增：自动识别逻辑 ★★★
                if (isAutoMode) {
                    const detected = detectActionFromLandmarks(results.poseLandmarks);
                    if (detected) {
                        if (detectedActionText) {
                            detectedActionText.textContent = detected; // 应该显示中文名称
                        }
                        applyActionChange(detected, { source: 'auto' });
                    } else {
                        // 如果没识别出来，保持上一个或者显示等待
                        // detectedActionText.textContent = "识别中...";
                    }
                }

                recordSessionFrame(results.poseLandmarks);

                // ★★★ 新增：计算卡路里 (后台计算，不更新UI) ★★★
                if (lastCalorieUpdate > 0) {
                    const dt = (now - lastCalorieUpdate) / 1000; // seconds
                    const burned = calculateCalories(dt);
                    caloriesBurned += burned;
                    // if (calorieCountElem) calorieCountElem.textContent = caloriesBurned.toFixed(1); // 移除实时显示
                }
                lastCalorieUpdate = now;

                if (now - lastSentTime > sendInterval) {
                    lastSentTime = now;
                    sendPoseDataToServer(results.poseLandmarks);
                }
            } else {
                 addFeedbackMessage("姿态不完整，请正对摄像头", 'warning');
            }
        } else {
            noPoseDetectedCounter++;
            if (noPoseDetectedCounter > 30) {
                addFeedbackMessage("未检测到人体，请确保您在画面中", 'warning');
            }
        }
        
        canvasCtx.restore();
    }

    // ★★★ 核心修改：将 fetch 改为 WebSocket 发送 ★★★
    function sendPoseDataToServer(landmarks) {
        // MediaPipe landmark 索引
        const P_LANDMARK_INDICES = {
            LEFT_SHOULDER: 11,
            LEFT_ELBOW: 13,
            LEFT_WRIST: 15,
            LEFT_HIP: 23,
            LEFT_KNEE: 25, // ★ 新增
            LEFT_ANKLE: 27 // ★ 新增
        };

        // 从 landmarks 数组中提取后端需要的点
        const shoulder = landmarks[P_LANDMARK_INDICES.LEFT_SHOULDER];
        const elbow = landmarks[P_LANDMARK_INDICES.LEFT_ELBOW];
        const wrist = landmarks[P_LANDMARK_INDICES.LEFT_WRIST];
        const hip = landmarks[P_LANDMARK_INDICES.LEFT_HIP];
        const knee = landmarks[P_LANDMARK_INDICES.LEFT_KNEE]; // ★ 新增
        const ankle = landmarks[P_LANDMARK_INDICES.LEFT_ANKLE]; // ★ 新增

        if (!shoulder || !elbow || !wrist || !hip || !knee || !ankle) {
            return;
        }

        // 构造符合 app.py 格式的 payload
        const payload = {
            action: currentAction,
            shoulder: [shoulder.x, shoulder.y, shoulder.z],
            elbow: [elbow.x, elbow.y, elbow.z],
            wrist: [wrist.x, wrist.y, wrist.z],
            hip: [hip.x, hip.y, hip.z],
            knee: [knee.x, knee.y, knee.z], // ★ 新增
            ankle: [ankle.x, ankle.y, ankle.z] // ★ 新增
        };

        if (transportMode === 'websocket') {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                return;
            }
            websocket.send(JSON.stringify(payload));
            return;
        }

        sendPoseDataViaHttp(payload);
    }
    
    // (保留) 这个函数现在不通过网络调用，但逻辑可以保留
    async function saveTrainingSession() {
        if (!sessionData.startTime || sessionData.reps === 0) return;
        try {
            const endTime = new Date();
            const duration = (endTime - sessionData.startTime) / 1000;
            
            // 计算平均分
            const avgScore = sessionData.scores.length > 0 
                ? sessionData.scores.reduce((a, b) => a + b, 0) / sessionData.scores.length 
                : 0;

            const payload = {
                start_time: sessionData.startTime.toISOString(),
                end_time: endTime.toISOString(),
                duration_seconds: Math.round(duration),
                action_name: currentActionElem.textContent !== '--' ? currentActionElem.textContent : '自由训练',
                rep_count: parseInt(repCountElem.textContent) || 0,
                avg_score: parseFloat(avgScore.toFixed(1))
            };

            const response = await fetch('/api/save_training_session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (response.ok) {
                addFeedbackMessage("训练记录已保存", "ok");
            } else {
                throw new Error('Server responded with error');
            }
        } catch (error) {
            console.error("Failed to save session:", error);
            addFeedbackMessage("保存训练记录失败", "error");
        }
    }

    // --- Initialize MediaPipe Pose ---
    function initializePose() {
        console.log("Initializing MediaPipe Pose...");
        const settings = performanceSettings[currentPerformanceTier];
        
        if (typeof Pose === 'undefined') {
            console.error("MediaPipe Pose library is not loaded!");
            addFeedbackMessage("核心库加载失败，请刷新页面", "error");
            return;
        }

        pose = new Pose({
            locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose@0.5.1675469404/${file}`
        });
        pose.setOptions({
            modelComplexity: settings.modelComplexity,
            smoothLandmarks: true,
            enableSegmentation: false,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5
        });
        pose.onResults(onResults);
        console.log("MediaPipe Pose initialized.");
    }

    // --- Camera Control ---
    async function startCamera() {
        if (isCameraOn) return;
        try {
            // ★★★ 新增：重置卡路里 ★★★
            caloriesBurned = 0;
            lastCalorieUpdate = Date.now();
            if (calorieCountElem) calorieCountElem.textContent = "0.0";
            resetSessionFrames();

            // ★ 新增 ★ 在启动摄像头时，初始化WebSocket连接
            httpRequestInFlight = false;
            transportMode = 'websocket';
            addFeedbackMessage('正在连接实时评分通道...', 'info');
            websocket = new WebSocket(WEBSOCKET_URL);
            wsConnectTimeout = setTimeout(() => {
                if (transportMode === 'websocket') {
                    switchToHttpTransport('实时评分通道连接超时，已切换为备用打分模式');
                }
            }, 4000);

            websocket.onopen = () => {
                clearWebsocketConnectTimer();
                console.log("WebSocket connection established.");
                addFeedbackMessage("服务器连接成功，请开始训练", "ok");
            };
            websocket.onmessage = handleServerMessage;
            websocket.onclose = () => {
                clearWebsocketConnectTimer();
                console.log("WebSocket connection closed.");
                if (transportMode === 'websocket' && isCameraOn) {
                    switchToHttpTransport('实时评分通道已断开，自动切换为备用打分模式');
                }
            };
            websocket.onerror = (error) => {
                console.error("WebSocket Error:", error);
                switchToHttpTransport('实时评分通道不可用，已切换为备用打分模式');
            };


            addFeedbackMessage('正在初始化摄像头...', 'info');
            currentPerformanceTier = performanceSelect.value;
            const settings = performanceSettings[currentPerformanceTier];
            initializePose();
            
            const videoContainer = document.querySelector('.video-container');
            videoElement.addEventListener('playing', () => {
                const videoWidth = videoElement.videoWidth;
                const videoHeight = videoElement.videoHeight;
                videoContainer.style.aspectRatio = `${videoWidth} / ${videoHeight}`;
                canvasElement.width = videoWidth;
                canvasElement.height = videoHeight;
                console.log(`Camera started with resolution: ${videoWidth}x${videoHeight}`);
            }, { once: true });

            camera = new Camera(videoElement, {
                onFrame: async () => {
                    const now = Date.now();
                    if (now - lastFrameProcessTime < settings.processInterval) return;
                    lastFrameProcessTime = now;
                    if (videoElement.readyState >= 2) {
                       await pose.send({ image: videoElement });
                    }
                },
                width: settings.resolution.width,
                height: settings.resolution.height
            });
            
            await camera.start();
            
            sessionData = { startTime: new Date(), reps: 0, scores: [] };
            feedbackHistory = [];
            feedbackLogList.innerHTML = '';
            isCameraOn = true;
            updateUIState();
            document.dispatchEvent(new CustomEvent('training:started'));
            // addFeedbackMessage('摄像头已开启，请开始训练', 'ok'); // 这条消息由 onopen 触发

        } catch (error) {
            console.error("摄像头启动失败:", error);
            let errorMsg = '摄像头启动失败';
            if (error.name === 'NotAllowedError') {
                errorMsg = '摄像头访问被拒绝，请在浏览器设置中允许访问。';
            } else if (error.name === 'NotFoundError') {
                errorMsg = '未找到可用的摄像头设备。';
            }
            addFeedbackMessage(errorMsg, 'error');
            isCameraOn = false;
            updateUIState();
        }
    }

    async function stopCamera() {
        if (!isCameraOn) return;
        if (camera) {
            camera.stop();
            camera = null;
        }
        
        // ★ 新增 ★ 关闭摄像头时，关闭WebSocket连接
        if (websocket) {
            websocket.onopen = null;
            websocket.onmessage = null;
            websocket.onclose = null;
            websocket.onerror = null;
            websocket.close();
            websocket = null;
        }
        clearWebsocketConnectTimer();
        transportMode = 'websocket';
        httpRequestInFlight = false;

        isCameraOn = false;
        updateUIState();
        document.dispatchEvent(new CustomEvent('training:stopped'));
        await saveTrainingSession();
        await exportSessionFrames();
        canvasCtx.clearRect(0, 0, canvasElement.width, canvasElement.height);
        updateDashboard({ action: '--', score: 0, reps: 0 });
        addFeedbackMessage('训练已结束', 'info');
    }

    // 3D 高斯泼溅示例：训练开始后做可视化/诊断兜底
    function showSplatDemoCardIfPresent() {
        const card = document.getElementById('daily-splat-card');
        if (card) card.style.display = '';

        const root = document.getElementById('splat-viewer');
        const fallback = document.getElementById('splat-demo-fallback');
        if (fallback) {
            fallback.style.display = '';
            fallback.textContent = '正在加载 3D 示例...';
        }
        if (root && !root.dataset.splatTouched) {
            root.dataset.splatTouched = '1';
        }
    }

    document.addEventListener('training:started', () => {
        if (trainingContext && trainingContext.forceShowSplatDemo) {
            showSplatDemoCardIfPresent();
        }

        // 5 秒后仍未就绪，提示可能的模块/渲染失败
        setTimeout(() => {
            if (window.__SPLAT_DEMO_READY__) return;

            const card = document.getElementById('daily-splat-card');
            const fallback = document.getElementById('splat-demo-fallback');
            if (card && trainingContext && trainingContext.forceShowSplatDemo) {
                card.style.display = '';
            }
            if (fallback) {
                fallback.style.display = '';
                fallback.textContent = '3D 示例未能启动渲染（可能是模块加载/Worker/模型解析失败）。请打开浏览器控制台查看 splat demo 报错。';
            }
        }, 5000);
    });

    function updateUIState() {
        if (isCameraOn) {
            startStopBtn.textContent = '结束训练';
            startStopBtn.classList.add('stop');
            statusDot.classList.add('active');
            statusText.textContent = '摄像头运行中';
            performanceSelect.disabled = true;
        } else {
            startStopBtn.textContent = '开始训练';
            startStopBtn.classList.remove('stop');
            statusDot.classList.remove('active');
            statusText.textContent = '摄像头已关闭';
            performanceSelect.disabled = false;
        }
    }

    // --- Event Listeners ---
    startStopBtn.addEventListener('click', () => {
        isCameraOn ? stopCamera() : startCamera();
    });
    
    performanceSelect.addEventListener('change', () => {
        currentPerformanceTier = performanceSelect.value;
        addFeedbackMessage(`性能模式已切换至: ${performanceSelect.options[performanceSelect.selectedIndex].text}`, 'info');
    });

    // --- Initial UI state ---
    updateUIState();
    addFeedbackMessage('请选择性能模式并开启摄像头', 'warning');
    console.log("Training page script loaded successfully.");
});