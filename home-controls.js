(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) {
    root.HomeControls = api;
    api.init();
  }
}(typeof window === 'undefined' ? null : window, function (windowRef) {
  const translations = Object.freeze({
    'zh-CN': Object.freeze({
      'card.reset.eyebrow': '工具 · 邮件提醒',
      'card.reset.title': 'Codex 重置提醒',
      'card.reset.description': '查看重置公告、预告与额度发放记录，绑定邮箱接收提醒，随时退订。',
      'card.reset.action': '订阅提醒 ↗',
      'card.ai.title': '多模态智能学习助手',
      'card.ai.description': '课堂音频转写、智能笔记、错题解析与交互式思维导图，支持独立访客体验。',
      'card.ai.action': '开始体验 ↗',
      'card.ai.eyebrow': '项目 · 智能学习',
      'control.language': 'EN',
      'hero.eyebrow': 'YilinPotato',
      'hero.title': '欢迎来到我的项目',
      'hero.description': '几个我做的东西。挑一个进去看看。',
      'card.new.eyebrow': '项目 · AI',
      'card.new.title': 'AI 运动评估系统',
      'card.new.description': '基于人工智能的运动姿态分析平台，实时识别动作、估算肌肉发力，帮助科学训练。',
      'card.new.action': '了解更多',
      'card.old.eyebrow': '项目 · 个人站',
      'card.old.title': '我的个人网站',
      'card.old.description': '经典版个人主页，内含留言板、网址大全、3D 模型查看器、图片隐写与文件保险箱。',
      'card.old.action': '进入',
      'card.mygo.eyebrow': '收录',
      'card.mygo.title': 'It\'s MyGO!!!!!',
      'card.mygo.description': '一个 MyGO!!!!! 主题专题页，乐队与角色介绍、官方主视觉、离线音乐播放与剧照画廊。',
      'card.mygo.action': '去看看',
      'card.myagents.eyebrow': '项目 · 多智能体',
      'card.myagents.title': 'MyAgents Lite 智能协作',
      'card.myagents.description': '轻量的数字员工与任务看板：本地保存、规则拆解，不依赖模型、数据库或后台服务。',
      'card.myagents.action': '进入平台',
      'card.jm.eyebrow': '工具 · QQ 机器人',
      'card.jm.title': '漫画查看器',
      'card.jm.description': '支持 QQ 机器人查询，快速预览漫画内容、在线阅读完整章节。',
      'card.jm.action': '打开',
    }),
    en: Object.freeze({
      'card.reset.eyebrow': 'Tool · Email alerts',
      'card.reset.title': 'Codex Reset Alerts',
      'card.reset.description': 'Track reset announcements and credits. Subscribe by email and unsubscribe anytime.',
      'card.reset.action': 'Subscribe ↗',
      'card.ai.title': 'Multimodal Learning Assistant',
      'card.ai.description': 'Lecture transcription, smart notes, question analysis and interactive mind maps. Try it in your own guest workspace.',
      'card.ai.action': 'Try it ↗',
      'card.ai.eyebrow': 'Project · Learning',
      'control.language': '中文',
      'hero.eyebrow': 'YilinPotato',
      'hero.title': 'Welcome to my projects',
      'hero.description': 'A few things I have made. Pick one to explore.',
      'card.new.eyebrow': 'Project · AI',
      'card.new.title': 'AI Exercise Assessment',
      'card.new.description': 'An AI-powered movement-analysis platform that recognizes exercise form and estimates muscle engagement for smarter training.',
      'card.new.action': 'Learn more',
      'card.old.eyebrow': 'Project · Personal site',
      'card.old.title': 'My Personal Website',
      'card.old.description': 'The classic personal homepage, with a message board, bookmarks, 3D model viewer, image steganography, and file vault.',
      'card.old.action': 'Enter',
      'card.mygo.eyebrow': 'Collection',
      'card.mygo.title': 'It\'s MyGO!!!!!',
      'card.mygo.description': 'A MyGO!!!!! feature page with band and character profiles, official visuals, offline music playback, and a scene gallery.',
      'card.mygo.action': 'Take a look',
      'card.myagents.eyebrow': 'Project · Multi-agent',
      'card.myagents.title': 'MyAgents Lite Collaboration',
      'card.myagents.description': 'Lightweight digital workers and task board with local storage and rule-based planning—no model, database, or backend required.',
      'card.myagents.action': 'Open platform',
      'card.jm.eyebrow': 'Tool · QQ bot',
      'card.jm.title': 'Comic Viewer',
      'card.jm.description': 'Look up comics through a QQ bot, preview content quickly, and read full chapters online.',
      'card.jm.action': 'Open',
    }),
  });

  function normalizeLanguage(value) {
    return value === 'en' ? 'en' : 'zh-CN';
  }

  function resolveTheme(savedTheme, prefersDark) {
    if (savedTheme === 'light' || savedTheme === 'dark') return savedTheme;
    return prefersDark ? 'dark' : 'light';
  }

  function createSecretTrigger({ count = 7, windowMs = 5000, now = Date.now, onTrigger }) {
    let clicks = [];
    return function registerClick() {
      const current = now();
      clicks = clicks.filter((timestamp) => current - timestamp <= windowMs);
      clicks.push(current);
      if (clicks.length >= count) {
        clicks = [];
        onTrigger();
      }
    };
  }

  function getStorage(storage) {
    try {
      return storage || windowRef.localStorage;
    } catch (error) {
      return null;
    }
  }

  function readStorage(storage, key) {
    try {
      return storage ? storage.getItem(key) : null;
    } catch (error) {
      return null;
    }
  }

  function writeStorage(storage, key, value) {
    try {
      if (storage) storage.setItem(key, value);
    } catch (error) {
      // Storage can be unavailable in private browsing; controls still work for this visit.
    }
  }

  function dispatchChange(documentRef, name, detail) {
    documentRef.dispatchEvent(new windowRef.CustomEvent(name, { detail }));
  }

  function init(options = {}) {
    if (!windowRef) return null;

    const documentRef = options.document || windowRef.document;
    const storage = getStorage(options.storage);
    const languageButton = documentRef.getElementById('languageToggle');
    const themeButton = documentRef.getElementById('themeToggle');
    const prefersDark = windowRef.matchMedia && windowRef.matchMedia('(prefers-color-scheme: dark)').matches;
    let language = 'zh-CN';
    let theme = 'light';

    function applyLanguage(nextLanguage) {
      language = normalizeLanguage(nextLanguage);
      documentRef.documentElement.lang = language;
      documentRef.querySelectorAll('[data-i18n]').forEach((node) => {
        node.textContent = translations[language][node.dataset.i18n];
      });
      languageButton.setAttribute('aria-label', language === 'en' ? 'Switch to Chinese' : 'Switch to English');
      themeButton.setAttribute('aria-label', language === 'en'
        ? (theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode')
        : (theme === 'dark' ? '切换浅色模式' : '切换深色模式'));
    }

    function applyTheme(nextTheme) {
      theme = resolveTheme(nextTheme, false);
      documentRef.documentElement.dataset.theme = theme;
      themeButton.setAttribute('aria-pressed', String(theme === 'dark'));
      themeButton.setAttribute('aria-label', language === 'en'
        ? (theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode')
        : (theme === 'dark' ? '切换浅色模式' : '切换深色模式'));
    }

    applyTheme(theme);
    applyLanguage(language);
    writeStorage(storage, 'site-language', language);
    writeStorage(storage, 'site-theme', theme);

    languageButton.addEventListener('click', () => {
      const nextLanguage = language === 'en' ? 'zh-CN' : 'en';
      applyLanguage(nextLanguage);
      writeStorage(storage, 'site-language', language);
      dispatchChange(documentRef, 'site:language-changed', language);
    });

    const registerSecretClick = createSecretTrigger({
      onTrigger: () => windowRef.location.assign('/site-admin/login'),
    });
    themeButton.addEventListener('click', () => {
      applyTheme(theme === 'dark' ? 'light' : 'dark');
      writeStorage(storage, 'site-theme', theme);
      dispatchChange(documentRef, 'site:theme-changed', theme);
      registerSecretClick();
    });

    return { language, theme };
  }

  return { createSecretTrigger, init, normalizeLanguage, resolveTheme, translations };
}));
