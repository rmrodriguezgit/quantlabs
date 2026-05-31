(function(){
  const bootTheme=()=>{
    const script=document.createElement('script');
    script.src='/dashboard/js/theme.js?v=20260531-theme-1';
    script.onload=()=>window.dispatchEvent(new Event('quantlabs-theme-ready'));
    document.head.appendChild(script);
  };
  if(!window.QuantLabsTheme) bootTheme();
  const aside = document.querySelector('aside:not(.sidebar)');
  if (!aside) return;
  const current = window.location.pathname.replace(/\/+$/, '/') || '/';
  const isActive = (href) => {
    if (href === '/dashboard/') return current === '/dashboard/';
    if (href === '/auth/me') return window.location.pathname === '/auth/me';
    if (href === '/') return window.location.pathname === '/';
    return current.startsWith(href);
  };
  const item = ({href, icon, label, target, danger}) => {
    const active = isActive(href) ? ' active' : '';
    const dangerStyle = danger ? ' style="margin-top:auto;color:#ff4466;"' : '';
    const targetAttr = target ? ` target="${target}"` : '';
    return `<a href="${href}" class="nav-link${active}"${targetAttr}${dangerStyle}><span class="nav-icon">${icon}</span> ${label}</a>`;
  };
  const platform = [
    {href:'/dashboard/', icon:'⬡', label:'Dashboard'},
    {href:'/jupyter/', icon:'📓', label:'Jupyter Diario', target:'_blank'},
    {href:'/jupyter-gpu/', icon:'⚙️', label:'Jupyter GPU', target:'_blank'},
    {href:'/llm/', icon:'🤖', label:'LLM Local', target:'_blank'},
    {href:'/dashboard/api/', icon:'⚡', label:'API QuantLab'},
    {href:'/dashboard/bitaxe/', icon:'⛏️', label:'BitAxe Miner'},
    {href:'/dashboard/harness/', icon:'🧠', label:'Agent Harness'},
    {href:'/dashboard/polymarket/', icon:'▰', label:'Polymarket Live'},
    {href:'/dashboard/validation/', icon:'✓', label:'Validation'}
  ];
  const account = [
    {href:'/auth/me', icon:'👤', label:'Mi cuenta'},
    {href:'/', icon:'🏠', label:'Landing'},
    {href:'/logout', icon:'⏻', label:'Logout', danger:true}
  ];
  const render = (adminItems=[]) => {
    aside.innerHTML = ['<div class="nav-label">Plataforma</div>', ...platform.map(item), '<div class="nav-divider"></div>', '<div class="nav-label">Vista</div>', '<div id="themeSlot"></div>', '<div class="nav-divider"></div>', '<div class="nav-label">Cuenta</div>', ...adminItems.map(item), ...account.map(item)].join('');
    const slot=document.getElementById('themeSlot');
    const mount=()=>{if(slot&&window.QuantLabsTheme){slot.innerHTML='';slot.appendChild(window.QuantLabsTheme.button());window.QuantLabsTheme.apply(window.QuantLabsTheme.current());}};
    mount();
    if(slot&&!window.QuantLabsTheme) window.addEventListener('quantlabs-theme-ready',mount,{once:true});
  };
  render();
  fetch('/auth/userinfo', {credentials:'same-origin', cache:'no-store'})
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (data && data.role === 'admin') render([
        {href:'/dashboard/system/', icon:'🖥️', label:'Infra / UPS'},
        {href:'/admin/users', icon:'⚙️', label:'Usuarios'}
      ]);
    })
    .catch(() => {});
})();
