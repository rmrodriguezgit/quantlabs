(function(){
  const storageKey='quantlabs-dashboard-theme';
  const root=document.documentElement;
  const preferred=()=>window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';
  const current=()=>localStorage.getItem(storageKey)||preferred();
  const apply=(theme)=>{
    const next=theme==='light'?'light':'dark';
    root.dataset.theme=next;
    localStorage.setItem(storageKey,next);
    document.querySelectorAll('[data-theme-label]').forEach(el=>{el.textContent=next==='dark'?'Modo noche':'Modo dia';});
    document.querySelectorAll('[data-theme-icon]').forEach(el=>{el.textContent=next==='dark'?'☾':'☀';});
  };
  const ensureCss=()=>{
    if(document.querySelector('link[data-theme-css]'))return;
    const link=document.createElement('link');
    link.rel='stylesheet';
    link.href='/dashboard/css/theme.css?v=20260531-theme-1';
    link.dataset.themeCss='1';
    document.head.appendChild(link);
  };
  const button=()=>{
    const btn=document.createElement('button');
    btn.className='theme-toggle';
    btn.type='button';
    btn.innerHTML='<span><span data-theme-icon>☾</span><span data-theme-label>Modo noche</span></span><span class="theme-knob"></span>';
    btn.addEventListener('click',()=>apply(root.dataset.theme==='dark'?'light':'dark'));
    return btn;
  };
  window.QuantLabsTheme={apply,current,button,ensureCss};
  ensureCss();
  apply(current());
})();
