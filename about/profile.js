(() => {
  const root = document.documentElement;
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  const isAbout = document.body.classList.contains('profile-page');
  const language = () => root.lang === 'en' ? 'en' : 'zh-CN';
  function translate() {
    const en = language() === 'en';
    document.querySelectorAll('[data-zh]').forEach(el => { el.textContent = en ? el.dataset.en : el.dataset.zh; });
    document.querySelectorAll('[data-alt-zh]').forEach(el => { el.alt = en ? el.dataset.altEn : el.dataset.altZh; });
    document.querySelectorAll('[data-label-zh]').forEach(el => { el.setAttribute('aria-label', en ? el.dataset.labelEn : el.dataset.labelZh); });
    document.querySelectorAll('[data-language]').forEach(el => { el.textContent = en ? '中文' : 'EN'; el.setAttribute('aria-label', en ? 'Switch to Chinese' : 'Switch to English'); });
    document.querySelectorAll('button[data-theme]').forEach(el => {
      el.textContent = root.dataset.theme === 'dark' ? (en ? 'Light ◐' : '浅色 ◐') : (en ? 'Dark ◐' : '深色 ◐');
      el.setAttribute('aria-pressed', String(root.dataset.theme === 'dark'));
    });
    if (isAbout) document.title = en ? 'Yilin Ma — Research & Projects' : '马亦麟 — 科研与项目';
  }
  if (isAbout) {
    root.lang = read('site-language') === 'en' ? 'en' : 'zh-CN';
    root.dataset.theme = 'light';
  }
  document.querySelectorAll('[data-language]').forEach(button => button.addEventListener('click', () => {
    if (!isAbout) document.getElementById('languageToggle').click();
    else {
      root.lang = language() === 'en' ? 'zh-CN' : 'en';
      write('site-language', root.lang);
      document.dispatchEvent(new CustomEvent('site:language-changed', {detail: root.lang}));
    }
    translate();
  }));
  document.querySelectorAll('button[data-theme]').forEach(button => button.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    write('site-theme', root.dataset.theme);
    document.dispatchEvent(new CustomEvent('site:theme-changed', {detail: root.dataset.theme}));
    translate();
  }));
  document.addEventListener('site:language-changed', translate);
  document.addEventListener('site:theme-changed', translate);
  translate();

  const chooser = document.getElementById('entry-dialog');
  // Keep keyboard focus inside either modal, including at the first/last control.
  document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('a[href], button:not([disabled]), [tabindex="0"]')];
    if (!controls.length) return;
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }));
  if (chooser) {
    const streakKey = 'site-project-choice-streak';
    const streak = () => Math.min(3, Math.max(0, Number(read(streakKey)) || 0));
    const show = () => {
      const wanted = new URL(location.href).searchParams.get('view') !== 'projects' && streak() < 3;
      if (wanted && !chooser.open) { chooser.showModal(); document.body.classList.add('choosing'); }
      if (!wanted && chooser.open) { chooser.close(); document.body.classList.remove('choosing'); }
    };
    const projects = event => {
      if (event) event.preventDefault();
      if (chooser.open) write(streakKey, String(streak() + 1));
      const url = new URL(location.href); url.searchParams.set('view', 'projects');
      history.pushState({}, '', url);
      show(); document.getElementById('about-entry').focus();
    };
    document.getElementById('choose-projects').addEventListener('click', projects);
    chooser.addEventListener('cancel', projects);
    chooser.querySelector('a[href="/about/"]').addEventListener('click', () => {
      write(streakKey, '0');
    });
    addEventListener('popstate', show); addEventListener('pageshow', show); show();
  }

  const viewer = document.getElementById('figure-dialog');
  if (viewer) {
    let source;
    document.querySelectorAll('a.figure-link').forEach(link => link.addEventListener('click', event => {
      event.preventDefault(); source = link;
      const thumb = link.querySelector('img');
      const full = viewer.querySelector('img'); full.src = link.href; full.alt = thumb.alt;
      viewer.showModal(); document.body.classList.add('viewing');
    }));
    viewer.querySelector('button').addEventListener('click', () => viewer.close());
    viewer.addEventListener('close', () => { document.body.classList.remove('viewing'); source?.focus(); });
    viewer.addEventListener('click', event => { if (event.target === viewer) viewer.close(); });
  }
})();
