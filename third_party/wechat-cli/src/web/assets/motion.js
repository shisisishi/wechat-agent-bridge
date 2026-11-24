/* ==========================================================================
   WeChat Assistant — Interactive Motion & Micro-Animation Engine
   Zero-dependency 60fps UI physics: sliding nav pill, cursor spotlight,
   3D hero tilt, circular theme morph, scroll-to-bottom pill, confetti burst
   ========================================================================== */

(function () {
  'use strict';

  const prefersReducedMotion = () =>
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- 1. Elastic Sliding Nav Indicator ---------- */
  function syncNavIndicator() {
    const nav = document.querySelector('.header-nav');
    if (!nav) return;
    let indicator = nav.querySelector('.nav-indicator');
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.className = 'nav-indicator';
      nav.prepend(indicator);
    }
    const active = nav.querySelector('.nav-btn.active');
    if (!active) {
      indicator.style.opacity = '0';
      return;
    }
    nav.classList.add('has-indicator');
    const left = active.offsetLeft - 4;
    const width = active.offsetWidth;
    indicator.style.width = `${width}px`;
    indicator.style.transform = `translate3d(${left}px, 0, 0)`;
    indicator.style.opacity = '1';
    if (nav.scrollWidth > nav.clientWidth + 4) {
      active.scrollIntoView({ behavior: 'smooth', inline: 'nearest', block: 'nearest' });
    }
  }
  window.__syncNavIndicator = syncNavIndicator;

  function initNavObserver() {
    const nav = document.querySelector('.header-nav');
    if (!nav) return;
    const mo = new MutationObserver(() => requestAnimationFrame(syncNavIndicator));
    mo.observe(nav, { subtree: true, attributes: true, attributeFilter: ['class'] });
    window.addEventListener('resize', () => requestAnimationFrame(syncNavIndicator));
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => requestAnimationFrame(syncNavIndicator));
    }
    requestAnimationFrame(syncNavIndicator);
    setTimeout(syncNavIndicator, 120);
  }

  /* ---------- 2. Cursor Spotlight & Gentle 3D Tilt ---------- */
  const SPOTLIGHT_SEL =
    '.card, .stats-card, .stats-hero-card, .contact-card, .fav-card, .search-result-item, .bt-item';
  let activeTiltEl = null;

  document.addEventListener(
    'pointermove',
    (e) => {
      const card = e.target && e.target.closest ? e.target.closest(SPOTLIGHT_SEL) : null;
      if (activeTiltEl && activeTiltEl !== card) {
        activeTiltEl.style.transform = '';
        activeTiltEl = null;
      }
      if (!card) return;
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      card.style.setProperty('--mx', `${x.toFixed(1)}px`);
      card.style.setProperty('--my', `${y.toFixed(1)}px`);

      if (!prefersReducedMotion() && card.classList.contains('stats-hero-card')) {
        const px = (x / Math.max(rect.width, 1) - 0.5) * 2;
        const py = (y / Math.max(rect.height, 1) - 0.5) * 2;
        const rx = (-py * 4.5).toFixed(2);
        const ry = (px * 4.5).toFixed(2);
        card.style.transform = `perspective(760px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-3px)`;
        activeTiltEl = card;
      }
    },
    { passive: true }
  );

  document.addEventListener('pointerout', (e) => {
    if (activeTiltEl && !activeTiltEl.contains(e.relatedTarget)) {
      activeTiltEl.style.transform = '';
      activeTiltEl = null;
    }
  });

  /* ---------- 3. Tactile Click Ripple Wave ---------- */
  const RIPPLE_SEL = '.btn, .nav-btn, #bridgeTodoFab, .icon-btn, .theme-toggle';
  document.addEventListener('pointerdown', (e) => {
    if (prefersReducedMotion() || e.button !== 0) return;
    const btn = e.target && e.target.closest ? e.target.closest(RIPPLE_SEL) : null;
    if (!btn || btn.disabled) return;
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 0.72;
    const wave = document.createElement('span');
    wave.className = 'ripple-wave';
    wave.style.width = `${size}px`;
    wave.style.height = `${size}px`;
    wave.style.left = `${e.clientX - rect.left - size / 2}px`;
    wave.style.top = `${e.clientY - rect.top - size / 2}px`;
    btn.appendChild(wave);
    setTimeout(() => wave.remove(), 620);
  });

  /* ---------- 4. Celebratory Micro-Confetti Burst ---------- */
  window.__burstConfetti = function (x, y) {
    if (prefersReducedMotion()) return;
    const palette = ['#07c160', '#34d399', '#38bdf8', '#fbbf24', '#a78bfa', '#fb7185'];
    const count = 14;
    for (let i = 0; i < count; i++) {
      const p = document.createElement('span');
      p.className = 'confetti-particle';
      const angle = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.45;
      const dist = 26 + Math.random() * 40;
      const tx = Math.cos(angle) * dist;
      const ty = Math.sin(angle) * dist - 14;
      const rot = (Math.random() - 0.5) * 520;
      p.style.left = `${x - 3}px`;
      p.style.top = `${y - 3}px`;
      p.style.background = palette[i % palette.length];
      p.style.borderRadius = i % 3 === 0 ? '50%' : '2px';
      p.style.setProperty('--tx', `${tx.toFixed(1)}px`);
      p.style.setProperty('--ty', `${ty.toFixed(1)}px`);
      p.style.setProperty('--rot', `${rot.toFixed(0)}deg`);
      document.body.appendChild(p);
      setTimeout(() => p.remove(), 720);
    }
  };

  /* ---------- 5. Page Transitions, Staggered Messages & Scroll-to-Bottom Pill ---------- */
  let scrollPill = null;
  let lastAnimatedPage = '';
  let lastAnimatedSession = '';

  function ensureScrollPill() {
    if (scrollPill) return scrollPill;
    scrollPill = document.createElement('button');
    scrollPill.className = 'scroll-bottom-btn';
    scrollPill.type = 'button';
    scrollPill.innerHTML = '<span>↓</span><span>回到最新消息</span>';
    scrollPill.onclick = () => {
      const mc = document.getElementById('messagesContainer');
      if (mc) mc.scrollTo({ top: mc.scrollHeight, behavior: 'smooth' });
    };
    document.body.appendChild(scrollPill);
    return scrollPill;
  }

  function bindMessagesScroll() {
    const mc = document.getElementById('messagesContainer');
    const pill = ensureScrollPill();
    if (!mc) {
      pill.classList.remove('visible');
      return;
    }
    const updatePill = () => {
      const distFromBottom = mc.scrollHeight - mc.scrollTop - mc.clientHeight;
      const rect = mc.getBoundingClientRect();
      pill.style.left = `${rect.left + rect.width / 2}px`;
      pill.classList.toggle('visible', distFromBottom > 280);
    };
    if (!mc.dataset.scrollBound) {
      mc.dataset.scrollBound = '1';
      mc.addEventListener('scroll', updatePill, { passive: true });
    }
    updatePill();
  }

  function animateRecentMessages() {
    if (prefersReducedMotion()) return;
    const mc = document.getElementById('messagesContainer');
    if (!mc) return;
    const activeSession = document.querySelector('.session-item.active')?.dataset?.username || '';
    const rows = mc.querySelectorAll('.message-row');
    if (!rows.length) return;
    if (activeSession && activeSession === lastAnimatedSession && mc.dataset.animatedCount === String(rows.length)) {
      return;
    }
    lastAnimatedSession = activeSession;
    mc.dataset.animatedCount = String(rows.length);

    const startIdx = Math.max(0, rows.length - 14);
    for (let i = startIdx; i < rows.length; i++) {
      const row = rows[i];
      row.classList.add('msg-anim');
      row.style.animationDelay = `${(i - startIdx) * 22}ms`;
    }
  }

  function initMainObserver() {
    const main = document.getElementById('mainContent');
    if (!main) return;
    const mo = new MutationObserver(() => {
      const activePage = document.querySelector('.nav-btn.active')?.dataset?.page || '';
      if (activePage && activePage !== lastAnimatedPage && !prefersReducedMotion()) {
        lastAnimatedPage = activePage;
        main.classList.remove('page-enter');
        void main.offsetWidth;
        main.classList.add('page-enter');
      }
      animateRecentMessages();
      bindMessagesScroll();
    });
    mo.observe(main, { childList: true });
    bindMessagesScroll();
  }

  /* ---------- 6. Circular View-Transition Theme Morph & Crisp Icons ---------- */
  const SUN_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';
  const MOON_SVG =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';

  function applyThemeIcon() {
    const btn = document.getElementById('themeBtn');
    if (!btn) return;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.innerHTML = isLight ? SUN_SVG : MOON_SVG;
    btn.title = isLight ? '切换到曜石深色模式' : '切换到瓷雾浅色模式';
  }

  function enhanceThemeAndRefresh() {
    if (typeof window.updateThemeBtn === 'function') {
      window.updateThemeBtn = applyThemeIcon;
    }
    applyThemeIcon();

    const origToggle = window.toggleTheme;
    if (typeof origToggle === 'function') {
      window.toggleTheme = function () {
        const btn = document.getElementById('themeBtn');
        const applySwitch = () => {
          origToggle();
          applyThemeIcon();
          requestAnimationFrame(syncNavIndicator);
        };
        if (!document.startViewTransition || prefersReducedMotion() || !btn) {
          applySwitch();
          return;
        }
        const rect = btn.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const endRadius = Math.hypot(
          Math.max(cx, window.innerWidth - cx),
          Math.max(cy, window.innerHeight - cy)
        );
        const transition = document.startViewTransition(applySwitch);
        transition.ready.then(() => {
          document.documentElement.animate(
            {
              clipPath: [
                `circle(0px at ${cx}px ${cy}px)`,
                `circle(${endRadius}px at ${cx}px ${cy}px)`,
              ],
            },
            {
              duration: 520,
              easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
              pseudoElement: '::view-transition-new(root)',
            }
          );
        }).catch(() => {});
      };
    }

    const origRefresh = window.manualRefresh;
    if (typeof origRefresh === 'function') {
      window.manualRefresh = async function () {
        const btn = document.getElementById('refreshBtn');
        if (btn) btn.classList.add('is-spinning');
        try {
          return await origRefresh.apply(this, arguments);
        } finally {
          if (btn) btn.classList.remove('is-spinning');
        }
      };
    }
  }

  /* ---------- 7. Draggable Floating To-Do Panel ---------- */
  function initTodoDrag() {
    const panel = document.getElementById('bridgeTodoPanel');
    const head = panel ? panel.querySelector('.bt-head') : null;
    if (!panel || !head) return;

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    head.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      dragging = true;
      const rect = panel.getBoundingClientRect();
      startX = e.clientX;
      startY = e.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      panel.classList.add('dragging');
      panel.style.left = `${rect.left}px`;
      panel.style.top = `${rect.top}px`;
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
      head.setPointerCapture(e.pointerId);
    });

    head.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const maxLeft = Math.max(8, window.innerWidth - panel.offsetWidth - 8);
      const maxTop = Math.max(8, window.innerHeight - panel.offsetHeight - 8);
      const nextLeft = Math.min(maxLeft, Math.max(8, startLeft + (e.clientX - startX)));
      const nextTop = Math.min(maxTop, Math.max(8, startTop + (e.clientY - startY)));
      panel.style.left = `${nextLeft}px`;
      panel.style.top = `${nextTop}px`;
    });

    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      panel.classList.remove('dragging');
    };
    head.addEventListener('pointerup', endDrag);
    head.addEventListener('pointercancel', endDrag);

    head.addEventListener('dblclick', (e) => {
      if (e.target.closest('button')) return;
      panel.style.left = '';
      panel.style.top = '';
      panel.style.right = '';
      panel.style.bottom = '';
    });
  }

  /* ---------- Bootstrap ---------- */
  function boot() {
    initNavObserver();
    initMainObserver();
    enhanceThemeAndRefresh();
    initTodoDrag();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
