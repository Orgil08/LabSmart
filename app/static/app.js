const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ROLE_LABELS = { admin:'Админ', staff:'Багш / Лабораторийн ажилтан', student:'Оюутан' };
const STATUS = {available:['Боломжтой','available'],in_use:['Ашиглаж байна','in_use'],maintenance:['Засвар үйлчилгээ','maintenance'],damaged:['Гэмтсэн','damaged'],lost:['Алдагдсан','lost'],retired:['Идэвхгүй','retired']};
const PRIORITY = {critical:['Яаралтай','critical'],high:['Өндөр','high'],medium:['Дунд','medium'],low:['Бага','low']};
const REQUEST_STATUS = {pending:['Хүлээгдэж байна','request-pending'],approved:['Батлагдсан','request-approved'],rejected:['Татгалзсан','request-rejected']};
const PAGE_NAMES = {dashboard:'Dashboard',equipment:'Төхөөрөмжүүд',qr:'QR авах / буцаах',myloans:'Миний төхөөрөмжүүд',requests:'Төхөөрөмжийн хүсэлт',schedule:'Хичээлийн хуваарь',demand:'Ирээдүйн хэрэгцээ',recommendations:'Ухаалаг зөвлөмж',reports:'Ашиглалт & тайлан',operations:'Аудит ба засвар',notifications:'Мэдэгдэл',users:'Хэрэглэгчид',settings:'Тохиргоо',profile:'Профайл'};
const PROFESSIONAL_PAGES = ['demand','recommendations','reports','operations'];
const ADMIN_PAGES = ['users','settings'];
let user = null;
let equipment = [];
let loans = [];
let requests = [];
let currentRoute = 'dashboard';
let searchTimer = null;
let cameraStream = null;
let cameraTimer = null;
let onboardingIndex = 0;
let appSettings = {default_loan_hours:24, low_stock_threshold:2, prediction_horizon_days:7, notifications_enabled:'1'};

async function api(path, method='GET', data) {
  const opts = {method, headers:{'Accept':'application/json'}, credentials:'same-origin'};
  if (method !== 'GET') {
    opts.headers['Content-Type'] = 'application/json';
    opts.headers['X-CSRF-Token'] = user?.csrf || '';
    opts.body = JSON.stringify(data ?? {});
  }
  const r = await fetch('/api' + path, opts);
  const contentType = r.headers.get('content-type') || '';
  const v = contentType.includes('application/json') ? await r.json() : await r.text();
  if (!r.ok) {
    if (r.status === 401 && !path.includes('/login') && !path.includes('/register')) showAuth('login');
    throw new Error(v?.error || 'Уучлаарай, мэдээллийг ачаалж чадсангүй.');
  }
  return v;
}

function setView(name) {
  $('#welcomeView').hidden = name !== 'welcome';
  $('#authView').hidden = name !== 'auth';
  $('#appView').hidden = name !== 'app';
}
function showWelcome() { stopCamera(); closeOpenDialogs(); setView('welcome'); window.scrollTo({top:0,behavior:'smooth'}); }
function showAuth(mode='login') { stopCamera(); closeOpenDialogs(); setView('auth'); toggleAuthMode(mode); window.scrollTo({top:0}); }
function toggleAuthMode(mode) {
  const login = mode !== 'register';
  $('#loginForm').hidden = !login;
  $('#registerForm').hidden = login;
  $('#authEyebrow').textContent = login ? 'ТАВТАЙ МОРИЛ' : 'ШИНЭ БҮРТГЭЛ';
  $('#authTitle').textContent = login ? 'Нэвтрэх' : 'Бүртгэл үүсгэх';
  $('#authSubtitle').textContent = login ? 'Бүртгэлтэй мэдээллээрээ нэвтэрнэ үү.' : 'Хэдхэн мэдээлэл бөглөөд LabSmart ашиглаж эхлээрэй.';
  $('#loginError').textContent = '';
  $('#registerError').textContent = '';
  if (!login) { $('#registerForm').reset(); updateStrength(''); } else { $('#loginPassword').focus(); }
}
function closeOpenDialogs() { $$('dialog[open]').forEach(d => d.close()); }
function showApp() { setView('app'); }
function initials(name='L') {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[parts.length - 1][0] : parts[0]?.slice(0,2) || 'L').toUpperCase();
}
function displayName() { return user?.name || user?.username || 'LabSmart'; }
function roleLabel() { return ROLE_LABELS[user?.role] || 'Оюутан'; }
function setIdentity() {
  const name = displayName();
  $('#userName').textContent = name;
  $('#userRole').textContent = roleLabel();
  $('#greetingName').textContent = user?.first_name || user?.username || 'LabSmart';
  $('#avatarInitials').textContent = initials(name);
  $('#topAvatar').textContent = initials(name);
  $('#profileAvatar').textContent = initials(name);
  $('#profileName').textContent = name;
  $('#profileRole').textContent = roleLabel();
  const isAdmin = user?.role === 'admin';
  const professional = ['admin','staff'].includes(user?.role);
  $$('.role-admin').forEach(el => el.hidden = !isAdmin);
  $$('.role-professional').forEach(el => el.hidden = !professional);
  $$('.professional-data').forEach(el => el.hidden = !professional);
  $('.professional-nav').hidden = !professional;
  $$('.admin-nav').forEach(el => el.hidden = !isAdmin);
  $$('.student-only').forEach(el => el.hidden = user?.role !== 'student');
}

async function enter(sessionUser) {
  user = sessionUser;
  showApp();
  setIdentity();
  routeTo(location.hash.slice(1) || 'dashboard', false);
  await Promise.allSettled([loadDashboard(), loadNotifications(), loadEquipment(), loadRequests()]);
  if (!user.onboarded) openOnboarding();
}
function logoutLocally() { user = null; stopCamera(); closeOpenDialogs(); showWelcome(); }
function canView(page) {
  if (ADMIN_PAGES.includes(page)) return user?.role === 'admin';
  if (PROFESSIONAL_PAGES.includes(page)) return ['admin','staff'].includes(user?.role);
  return true;
}
function routeTo(page='dashboard', push=true) {
  if (!PAGE_NAMES[page] || !canView(page)) page = 'dashboard';
  currentRoute = page;
  if (push) history.pushState({page}, '', '#' + page);
  $$('.page').forEach(section => section.hidden = section.dataset.page !== page);
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.nav === page));
  $('#breadcrumb').textContent = PAGE_NAMES[page];
  $('#mainContent').focus({preventScroll:true});
  closeSidebar();
  const loaders = {dashboard:loadDashboard,equipment:loadEquipment,qr:prepareQr,myloans:loadLoans,requests:loadRequests,schedule:loadSchedule,demand:loadDemand,recommendations:loadRecommendations,reports:loadReports,operations:loadOperations,notifications:loadNotifications,users:loadUsers,settings:loadSettings,profile:renderProfile};
  loaders[page]?.();
}
function openDialog(id){ const d=$('#'+id); if(d?.showModal) d.showModal(); }
function closeDialog(id){ $('#'+id)?.close(); }
function toast(message,type='success',title='Амжилттай'){
  const d=$('#toastDialog'); d.className='toast-dialog '+(type==='warning'?'warning':type==='error'?'error':''); $('#toastTitle').textContent=title; $('#toastMessage').textContent=message; $('#toastIcon').textContent=type==='error'?'!':type==='warning'?'!':'✓'; d.show(); clearTimeout(toast.timer); toast.timer=setTimeout(()=>d.open&&d.close(),3200);
}
function setBusy(button,busy,text){ if(!button)return; button.disabled=busy; const label=$('.button-label',button); const spinner=$('.button-spinner',button); if(label){if(!label.dataset.default)label.dataset.default=label.textContent;if(text)label.textContent=busy?text:label.dataset.default;} if(spinner)spinner.hidden=!busy; }
function statusPill(status){ const x=STATUS[status]||[status,status]; return `<span class="status-pill status-${esc(x[1])}">${esc(x[0])}</span>`; }
function requestPill(status){ const x=REQUEST_STATUS[status]||REQUEST_STATUS.pending; return `<span class="status-pill ${esc(x[1])}">${esc(x[0])}</span>`; }
function priorityPill(priority){ const x=PRIORITY[priority]||PRIORITY.low; return `<span class="priority priority-${x[1]}">${esc(x[0])}</span>`; }
function fmtDate(value,withTime=true){ if(!value)return '—'; const d=new Date(value); if(Number.isNaN(d.getTime()))return value; return new Intl.DateTimeFormat('mn-MN',{year:'numeric',month:'2-digit',day:'2-digit',...(withTime?{hour:'2-digit',minute:'2-digit'}:{})}).format(d); }
function fmtRelative(value){ const d=new Date(value),now=new Date();const mins=Math.round((now-d)/60000);if(mins<60)return `${Math.max(1,mins)} мин өмнө`;const h=Math.round(mins/60);if(h<24)return `${h} цаг өмнө`;return `${Math.round(h/24)} өдрийн өмнө`; }
function emptyInline(msg,action=''){ return `<div class="state-block inline-empty"><div class="empty-icon">⌁</div><b>${esc(msg)}</b>${action?`<button class="secondary-button" ${action}>Үйлдэл</button>`:''}</div>`; }
function showPageError(page,msg){ const section=$(`.page[data-page="${page}"]`); if(!section)return; section.querySelector('.page-error')?.remove(); const n=document.createElement('div');n.className='state-block page-error';n.innerHTML=`<div class="empty-icon">!</div><b>Уучлаарай, мэдээллийг ачаалж чадсангүй.</b><p>${esc(msg)}</p><button class="secondary-button" data-retry-page="${esc(page)}">Дахин оролдох</button>`;section.appendChild(n); }

async function loadDashboard(){
  try{
    const data=await api('/dashboard');
    appSettings.default_loan_hours = Number(data.default_loan_hours || appSettings.default_loan_hours);
    $('#dashboardKpis').innerHTML=[
      ['Нийт төхөөрөмж',data.total,'Бүх бүртгэл','▦'],
      ['Одоогоор ашиглаж байгаа',data.in_use,'Буцаагдаагүй олголт','◷'],
      ['Засвар үйлчилгээ',data.maintenance,'Одоогоор засварт','⌂'],
      ['Хугацаа хэтэрсэн',data.overdue,'Яаралтай шалгах','!']
    ].map(k=>`<article class="kpi-card"><div class="kpi-top"><span>${k[0]}</span><span class="kpi-icon">${k[3]}</span></div><div class="kpi-value">${esc(k[1])}</div><div class="kpi-meta">${esc(k[2])}</div></article>`).join('');
    renderUtilizationChart(data.utilization||[]);renderStatusChart(data.status_chart||[]);renderDemandList(data.demand||[]);renderRecent(data.recent||[]);renderDashboardAlerts(data.alerts||[]);
  }catch(e){showPageError('dashboard',e.message)}
}
function renderUtilizationChart(rows){ const max=Math.max(1,...rows.map(x=>x.value));$('#utilizationChart').innerHTML=rows.length?`<div class="bar-chart">${rows.map(r=>`<div class="bar-col"><div class="bar-track"><div class="bar" style="height:${Math.max(5,r.value/max*100)}%" title="${esc(r.value)} олголт"></div></div><span>${esc(r.label)}</span></div>`).join('')}</div>`:emptyInline('Сүүлийн 7 хоногийн мэдээлэл алга.'); }
function renderStatusChart(rows){ const total=rows.reduce((a,b)=>a+b.value,0);if(!rows.length){$('#statusChart').innerHTML=emptyInline('Төлөвийн мэдээлэл алга.');return;}let start=0;const vars=['--green','--blue','--yellow','--purple'];const stops=[];rows.forEach((r,i)=>{const end=start+(total? r.value/total*100:0);stops.push(`${vars[i%vars.length]} ${start}% ${end}%`);start=end;});$('#statusChart').innerHTML=`<div class="donut-area"><div class="donut" style="background:conic-gradient(${stops.join(',')})"><div><strong>${total}</strong><small>бүртгэл</small></div></div><div class="legend">${rows.map((r,i)=>`<div class="legend-row" style="--dot:${vars[i%vars.length]}"><span>${esc(r.label)}</span><b>${esc(r.value)}</b></div>`).join('')}</div></div>`; }
function renderDemandList(rows){$('#dashboardDemand').innerHTML=rows.length?rows.map(r=>`<div class="demand-item"><span class="device-icon">↗</span><div class="demand-main"><b>${esc(r.equipment_name)}</b><small>Ирэх 7 хоног · Одоо ${esc(r.available)} ширхэг</small></div><span class="value-chip ${r.processed_qty>r.available?'warning':''}">${esc(r.range_low)}–${esc(r.range_high)} хэрэгтэй</span></div>`).join(''):emptyInline('Хүлээгдэж буй хэрэгцээний мэдээлэл одоогоор алга.');}
function renderRecent(rows){$('#recentActivities').innerHTML=rows.length?rows.map(r=>`<div class="activity-item"><span class="audit-badge">${r.entity_type==='loan'?'↗':r.entity_type==='maintenance'?'⌂':'•'}</span><div class="activity-main"><b>${esc(r.action)}</b><small>${esc(r.actor)} · ${fmtRelative(r.created_at)}</small></div></div>`).join(''):emptyInline('Сүүлийн үйлдэл одоогоор бүртгэгдээгүй.');}
function renderDashboardAlerts(rows){$('#dashboardAlerts').innerHTML=rows.length?rows.map(n=>`<button class="alert-row ${n.read_at?'':'unread'}" data-nav="notifications"><span class="notification-dot ${esc(n.severity)}"></span><span><b>${esc(n.title)}</b><small>${esc(n.message)}</small></span>${n.read_at?'':'<strong>Шинэ</strong>'}</button>`).join(''):emptyInline('Одоогоор яаралтай анхаарах зүйл алга.');}

async function loadEquipment(){
  $('#equipmentLoading').hidden=false;$('#equipmentEmpty').hidden=true;$('#equipmentRows').innerHTML='';
  try{const q=$('#equipmentSearch')?.value||'';const category=$('#equipmentCategory')?.value||'';const location=$('#equipmentLocation')?.value||'';const status=$('#equipmentStatus')?.value||'';const qs=new URLSearchParams({q,category,location,status});equipment=await api('/equipment?'+qs.toString());updateEquipmentFilters();renderEquipment();}catch(e){showPageError('equipment',e.message)}finally{$('#equipmentLoading').hidden=true;}
}
function updateEquipmentFilters(){const curCat=$('#equipmentCategory').value,curLoc=$('#equipmentLocation').value;const cats=[...new Set(equipment.map(e=>e.category))].sort(),locs=[...new Set(equipment.map(e=>e.location))].sort();$('#equipmentCategory').innerHTML='<option value="">Бүх ангилал</option>'+cats.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');$('#equipmentCategory').value=cats.includes(curCat)?curCat:'';$('#equipmentLocation').innerHTML='<option value="">Бүх байршил</option>'+locs.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');$('#equipmentLocation').value=locs.includes(curLoc)?curLoc:'';$('#categories').innerHTML=cats.map(x=>`<option value="${esc(x)}"></option>`).join('');$('#locations').innerHTML=locs.map(x=>`<option value="${esc(x)}"></option>`).join('');const counts={available:0,in_use:0,maintenance:0,retired:0};equipment.forEach(x=>counts[x.status]=(counts[x.status]||0)+1);$('#equipmentSummary').innerHTML=[['Нийт',equipment.length],['Боломжтой',counts.available||0],['Ашиглаж байна',counts.in_use||0],['Засвар үйлчилгээ',counts.maintenance||0]].map(x=>`<div class="mini-stat"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');}
function renderEquipment(){const rows=equipment;$('#equipmentEmpty').hidden=rows.length!==0;$('#equipmentRows').innerHTML=rows.map(x=>`<tr><td><div class="device-cell"><span class="device-icon">▦</span><div><strong>${esc(x.name)}</strong><small>${esc(x.location)}</small></div></div></td><td>${esc(x.code)}</td><td>${esc(x.category)}</td><td><span class="quantity-badge">1</span></td><td>${statusPill(x.status)}</td><td>${esc(x.location)}</td><td><div class="row-actions"><button class="row-button" data-detail-equipment="${x.id}">Дэлгэрэнгүй</button>${x.status==='available'?`<button class="row-button" data-borrow-equipment="${x.id}">Одоо авах</button>`:''}${user?.role==='student'?`<button class="row-button" data-open-request="" data-request-device="${esc(x.name)}">Хүсэлт</button>`:''}${user?.role==='admin'?`<button class="row-button" data-edit-equipment="${x.id}">Засварлах</button>`:''}</div></td></tr>`).join('');$('#equipmentCount').textContent=`${rows.length} төхөөрөмж харуулж байна`+(rows.length!==equipment.length?` · Шүүлтүүртэй`:'');}
function clearEquipmentFilters(){$('#equipmentSearch').value='';$('#equipmentCategory').value='';$('#equipmentLocation').value='';$('#equipmentStatus').value='';loadEquipment();}

async function openEquipmentDetail(id){try{const x=await api('/equipment/'+id);$('#detailTitle').textContent=x.name;$('#detailSubtitle').textContent=`${x.code} · ${x.location}`;$('#detailStatus').innerHTML=statusPill(x.status);$('#detailPrimaryFact').textContent=x.current_loan?`Одоогоор ${x.current_loan.borrower} ашиглаж байна.`:x.status==='available'?'Одоо авах боломжтой.':'Одоогоор авах боломжгүй.';$('#detailDeviceIcon').innerHTML=`<span class="device-illustration">▦</span>`;$('#detailFacts').innerHTML=[['Төхөөрөмжийн дугаар',x.code],['Ангилал',x.category],['Тоо хэмжээ','1 төхөөрөмж'],['Байршил',x.location],['Төлөв',STATUS[x.status]?.[0]||x.status],['Сүүлд шинэчилсэн',fmtDate(x.updated_at)]].map(f=>`<div><span>${esc(f[0])}</span><b>${esc(f[1])}</b></div>`).join('');$('#advancedFacts').innerHTML=[['Серийн дугаар',x.serial||'—'],['Төхөөрөмжийн ID',x.id],['Ашигласан тоо',x.usage_count+' удаа'],['Засварын бүртгэл',x.maintenance_count+' удаа'],['Тайлбар',x.notes||'—']].map(f=>`<div><span>${esc(f[0])}</span><b>${esc(f[1])}</b></div>`).join('');$('#detailActions').innerHTML=(x.status==='available'?`<button class="primary-button" data-borrow-equipment-dialog="${x.id}">QR-аар авах</button>`:'')+(user?.role==='student'?`<button class="secondary-button" data-open-request data-request-device="${esc(x.name)}">Хүсэлт гаргах</button>`:'')+(x.current_loan && (user.role!=='student'||x.current_loan.borrower?.toLowerCase().includes((user.first_name||user.username).toLowerCase()))?`<button class="secondary-button" data-return-loan="${x.current_loan.id}" data-return-name="${esc(x.name)}">Буцаах</button>`:'')+(user?.role==='admin'?`<button class="secondary-button" data-edit-from-detail="${x.id}">Засварлах</button>`:'');const hist=await api('/equipment/'+id+'/history');const all=[...(hist.changes||[]),...(hist.events||[])];$('#equipmentHistory').innerHTML=all.length?all.map(h=>`<div class="history-item"><b>${esc(h.action)}</b><small>${esc(h.username||h.actor||'system')} · ${fmtDate(h.created_at)}</small><small>${esc(h.detail||'')}</small></div>`).join(''):emptyInline('Өмнөх түүх бүртгэгдээгүй.');openDialog('equipmentDetailDialog');}catch(e){toast(e.message,'error','Алдаа')}}
function openEquipmentEditor(id){const f=$('#equipmentForm');f.reset();f.elements.id.value='';let item=null;if(id){item=equipment.find(x=>x.id===Number(id));if(item){['code','name','category','location','serial','status','notes'].forEach(k=>f.elements[k].value=item[k]??'');f.elements.id.value=item.id;}}$('#equipmentDialogTitle').textContent=item?'Төхөөрөмж засах':'Төхөөрөмж нэмэх';$('#equipmentError').textContent='';openDialog('equipmentDialog');}

async function openBorrowDialog(id, requestId=0){const x=equipment.find(e=>e.id===Number(id))||await api('/equipment/'+id);if(x.status!=='available'){toast('Энэ төхөөрөмж одоогоор авах боломжгүй байна.','warning','Анхаарах');return;}$('#borrowEquipmentName').textContent=`${x.name} · ${x.code}`;$('#borrowFacts').innerHTML=[['Байршил',x.location],['Төлөв','Боломжтой'],['Төхөөрөмжийн дугаар',x.code],['Буцаалт','Стандарт хугацаагаар']].map(f=>`<div><small>${f[0]}</small><b>${esc(f[1])}</b></div>`).join('');const hours=Math.max(1,Number(appSettings.default_loan_hours||24));const due=new Date(Date.now()+hours*3600*1000);due.setMinutes(0,0,0);$('#dueAt').value=new Date(due.getTime()-due.getTimezoneOffset()*60000).toISOString().slice(0,16);$('#borrowForm').dataset.equipmentId=x.id;$('#borrowForm').dataset.requestId=requestId||'';$('#borrowError').textContent='';openDialog('borrowDialog');}
function openReturnDialog(loanId,name){$('#returnForm').elements.loan_id.value=loanId;$('#returnEquipmentName').textContent=name||'Төхөөрөмж';$('#returnError').textContent='';openDialog('returnDialog');}

async function loadLoans(){try{loans=await api('/loans?mine=1');const open=loans.filter(l=>!l.returned_at),overdue=open.filter(l=>l.overdue);$('#loanSummary').innerHTML=[['Одоогоор ашиглаж байгаа',open.length],['Хугацаа хэтэрсэн',overdue.length],['Нийт олголт',loans.length],['Боломжтой төхөөрөмж',equipment.filter(e=>e.status==='available').length]].map(x=>`<div class="mini-stat"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');$('#loansList').innerHTML=loans.length?loans.map(l=>`<article class="loan-card"><span class="device-icon">◷</span><div class="loan-main"><b>${esc(l.name)}</b><small>${esc(l.code)} · ${esc(l.location)}</small><small>Авсан: ${fmtDate(l.issued_at)} · Буцаах: ${fmtDate(l.due_at)}</small></div><span class="value-chip ${l.overdue?'warning':''}">${l.returned_at?'Буцаасан':l.overdue?'Хугацаа хэтэрсэн':'Буцаах '+fmtDate(l.due_at,false)}</span>${!l.returned_at?`<button class="row-button" data-return-loan="${l.id}" data-return-name="${esc(l.name)}">Буцаах</button>`:''}</article>`).join(''):emptyInline('Одоогоор танд олгогдсон төхөөрөмж алга.');}catch(e){showPageError('myloans',e.message)}}

async function loadRequests(){try{requests=await api('/requests');const counts={pending:0,approved:0,rejected:0};requests.forEach(r=>counts[r.status]=(counts[r.status]||0)+1);$('#requestSummary').innerHTML=[['Хүлээгдэж байна',counts.pending],['Батлагдсан',counts.approved],['Татгалзсан',counts.rejected]].map(x=>`<div class="request-summary-card"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');$('#requestList').innerHTML=requests.length?requests.map(r=>{const prof=['admin','staff'].includes(user?.role);const canApprove=prof&&r.status==='pending';const hasAsset=r.available_now>0;return `<article class="request-card"><div class="request-card-head"><div><h3>${esc(r.equipment_name)}</h3><p>${esc(r.course)} · ${esc(r.lab_no)} · ${fmtDate(r.start_at)}</p>${prof?`<small>Хүсэлт гаргасан: ${esc(r.requester)}</small>`:''}</div>${requestPill(r.status)}</div><div class="request-facts"><div><span>Хүссэн тоо</span><b>${r.requested_qty}</b></div><div><span>Баталсан</span><b>${r.approved_qty||0}</b></div><div><span>Одоогийн нөөц</span><b>${r.available_now}</b></div><div><span>Буцаах/ашиглах огноо</span><b>${fmtDate(r.start_at,false)}</b></div></div>${r.status==='rejected'?`<div class="request-note warning">Шалтгаан: ${esc(r.rejection_reason||'Батлах боломжгүй байна.')}</div>`:''}<div class="request-actions">${canApprove?`<button class="primary-button" data-request-status="${r.id}" data-status="approved">Батлах</button><button class="secondary-button" data-request-status="${r.id}" data-status="rejected">Татгалзах</button>`:''}${r.status==='approved'&&hasAsset?`<button class="primary-button" data-request-checkout="${r.id}" data-equipment-name="${esc(r.equipment_name)}">QR-аар авах</button>`:''}${r.status==='pending'&&user?.role==='student'?`<span class="muted">Баталгаажсаны дараа QR-аар авах боломжтой.</span>`:''}</div></article>`;}).join(''):emptyInline('Одоогоор төхөөрөмжийн хүсэлт алга.');}catch(e){showPageError('requests',e.message)}}
async function openRequestDialog(deviceName=''){try{const [sessions,eq] = await Promise.all([api('/sessions'),api('/equipment')]);const upcoming=sessions.filter(s=>new Date(s.start_at)>new Date());$('#requestSession').innerHTML=upcoming.length?upcoming.map(s=>`<option value="${s.id}">${esc(s.course)} · ${esc(s.lab_no)} · ${fmtDate(s.start_at)}</option>`).join(''):'<option value="">Ирээдүйн session алга</option>';const names=[...new Set(eq.filter(x=>x.status!=='retired').map(x=>x.name))].sort();$('#requestEquipmentName').innerHTML=names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');if(deviceName && names.includes(deviceName))$('#requestEquipmentName').value=deviceName;$('#requestError').textContent='';openDialog('requestDialog');}catch(e){toast(e.message,'error','Алдаа')}}

async function loadSchedule(){try{const rows=await api('/sessions');$('#scheduleRows').innerHTML=rows.map(s=>`<tr><td><strong>${esc(s.course)}</strong></td><td>${esc(s.lab_no)}</td><td>${fmtDate(s.start_at)}</td><td>${esc(s.planned_students)}</td><td>${esc(s.planned_teams)}</td><td><span class="status-pill status-info">Төлөвлөсөн</span></td></tr>`).join('');$('#scheduleEmpty').hidden=rows.length>0;}catch(e){showPageError('schedule',e.message)}}

async function loadDemand(){try{const rows=await api('/predictions');$('#demandGrid').innerHTML=rows.length?rows.slice(0,12).map(p=>`<article class="demand-card"><div class="demand-card-top"><div><h3>${esc(p.equipment_name)}</h3><small>${esc(p.course)} · ${esc(p.lab_no)} · ${fmtDate(p.start_at)}</small></div>${p.status==='low'?'<span class="status-pill status-available">✓ Хангалттай</span>':'<span class="status-pill status-maintenance">⚠ Анхаарах</span>'}</div><div class="range"><strong>${p.range_low}–${p.range_high}</strong><span>хэрэг болж магадгүй</span></div><div class="demand-availability"><div><small>Одоогоор</small><b>${p.available_now} ширхэг</b></div><div><small>Таамаг</small><b>${p.processed_qty} ширхэг</b></div></div><div class="demand-explanation">${p.status==='low'?'Одоогийн нөөц хангалттай байна.':'Нөөц хүрэлцэхгүй байж болзошгүй. Зөвлөмжөөс дараагийн алхмыг харна уу.'}</div><div class="status-message ${p.status==='low'?'ok':'warn'}">${p.status==='low'?'✓ Нөөц хангалттай':'⚠ Анхаарах шаардлагатай'}</div></article>`).join(''):emptyInline('Ирээдүйн хэрэгцээний мэдээлэл одоогоор алга.');const m=await api('/ml/summary');$('#modelUsedLabel').textContent=m.latest?.model_name||'Түүх хүрэлцээгүй';$('#modelSummaryRows').innerHTML=(m.runs||[]).slice(0,6).map(r=>`<tr><td>${esc(r.model_name)}</td><td>${r.train_rows||0}</td><td>${r.test_rows||0}</td><td>${r.mae==null?'—':r.mae}</td><td>${r.rmse==null?'—':r.rmse}</td><td>${r.bias==null?'—':r.bias}</td></tr>`).join('')||'<tr><td colspan="6">Одоогоор batch үр дүн алга.</td></tr>';}catch(e){showPageError('demand',e.message)}}
async function loadRecommendations(){try{const rows=await api('/recommendations');$('#recommendationList').innerHTML=rows.length?rows.map(r=>`<article class="recommendation-card"><div class="rec-top"><div><h3>${esc(r.equipment_name)}</h3><p>${esc(r.course)} · ${esc(r.lab_no)} · ${fmtDate(r.start_at)}</p></div>${priorityPill(r.priority)}</div><div class="rec-grid"><div class="rec-fact"><span>Юу болж байна?</span><b>${r.shortage_qty>0?`${r.shortage_qty} ширхэг дутна`:'Хүрэлцэх төлөвтэй'}</b></div><div class="rec-fact"><span>Одоогийн нөөц</span><b>${r.available_interval} ширхэг</b></div><div class="rec-fact"><span>Бэлдэх хэмжээ</span><b>${r.allocation_qty} ширхэг</b></div></div><div class="rec-explain"><b>Яагаад?</b> ${esc(r.explanation)}<br><br><b>Юу хийх вэ?</b> ${esc(r.suggestion)}</div><div class="rec-actions">${r.decision_status==='open'?`<button class="primary-button" data-rec-status="${r.id}" data-status="accepted">Зөвшөөрөх</button><button class="secondary-button" data-rec-status="${r.id}" data-status="dismissed">Хаах</button>`:`<span class="status-pill ${r.decision_status==='accepted'?'status-available':'status-retired'}">${r.decision_status==='accepted'?'Зөвшөөрсөн':'Хаасан'}</span>`}</div></article>`).join(''):emptyInline('Одоогоор шинэ зөвлөмж алга.');}catch(e){showPageError('recommendations',e.message)}}
async function loadReports(){try{const d=await api('/reports');$('#reportKpis').innerHTML=[['Ашиглалтын түвшин',d.utilization+'%','Идэвхтэй төхөөрөмжийн харьцаа'],['Нийт олголт',d.loans.reduce((a,x)=>a+(x.total_loans||0),0),'Төхөөрөмжийн олголт'],['Нээлттэй олголт',d.loans.reduce((a,x)=>a+(x.open_loans||0),0),'Одоогоор буцаагдаагүй'],['Засварын бүртгэл',d.maintenance.length,'Сүүлийн ажлууд']].map(x=>`<article class="kpi-card"><div class="kpi-top"><span>${x[0]}</span><span class="kpi-icon">▤</span></div><div class="kpi-value">${esc(x[1])}</div><div class="kpi-meta">${esc(x[2])}</div></article>`).join('');renderRanking('#topUsed',d.top_used);renderRanking('#lowUsed',d.low_used);renderRanking('#userUsageReport',(d.user_usage||[]).map(x=>({name:x.borrower,uses:x.uses})));$('#loanReport').innerHTML=d.loans.length?d.loans.map(x=>`<div class="table-row"><div class="activity-main"><b>${esc(x.name)}</b><small>Нийт ${x.total_loans||0} · Нээлттэй ${x.open_loans||0}</small></div><span class="value-chip ${x.overdue?'warning':''}">${x.overdue||0} хэтэрсэн</span></div>`).join(''):emptyInline('Буцаалтын мэдээлэл алга.');$('#maintenanceReport').innerHTML=d.maintenance.length?d.maintenance.map(x=>`<div class="table-row"><div class="activity-main"><b>${esc(x.name)}</b><small>${esc(x.issue)} · ${esc(PRIORITY[x.priority]?.[0]||x.priority)}</small></div><span class="muted">${x.completed_at?'Дууссан':x.scheduled_at?fmtDate(x.scheduled_at):'Төлөвлөгдөөгүй'}</span></div>`).join(''):emptyInline('Засварын мэдээлэл алга.');}catch(e){showPageError('reports',e.message)}}
function renderRanking(selector,rows=[]){const el=$(selector);if(!el)return;el.innerHTML=rows.length?rows.map((x,i)=>`<div class="ranking-item"><span class="value-chip">${i+1}</span><div class="activity-main"><b>${esc(x.name||'—')}</b><small>${x.uses||0} удаа ашиглагдсан</small></div><strong>${x.uses||0}</strong></div>`).join(''):emptyInline('Мэдээлэл алга.');}
async function loadOperations(){try{await Promise.all([loadAudit(),loadMaintenance()])}catch(e){showPageError('operations',e.message)}}
async function loadAudit(){const rows=await api('/audit');$('#auditList').innerHTML=rows.length?rows.map(x=>`<div class="audit-item"><span class="audit-badge">${x.entity_type==='loan'?'↗':x.entity_type==='maintenance'?'⌂':x.entity_type==='auth'?'•':'▦'}</span><div class="audit-main"><b>${esc(x.action)}</b><small>${esc(x.actor)} · ${fmtDate(x.created_at)}</small><small>${esc(x.detail||'')}</small></div><span class="audit-time">${esc(x.entity_type)}</span></div>`).join(''):emptyInline('Үйлдлийн түүх одоогоор алга.');}
async function loadMaintenance(){const rows=await api('/maintenance');$('#maintenanceList').innerHTML=rows.length?rows.map(x=>`<div class="maintenance-row"><div><b>${esc(x.name)}</b><small>${esc(x.issue)} · ${esc(x.code)}</small><small>${x.scheduled_at?'Төлөвлөсөн: '+fmtDate(x.scheduled_at):'Огноо товлоогүй'}</small></div><div class="maintenance-actions">${priorityPill(x.priority)}${x.completed_at?'<span class="status-pill status-available">Дууссан</span>':`<button class="row-button" data-finish-maintenance="${x.id}">Дуусгах</button>`}</div></div>`).join(''):emptyInline('Засварын ажил одоогоор бүртгэгдээгүй.');}
function createMaintenance(){const panel=$('#maintenanceFormPanel');panel.hidden=false;const opts=equipment.map(x=>`<option value="${x.id}">${esc(x.name)} · ${esc(x.code)}</option>`).join('');$('#maintenanceEquipment').innerHTML=opts||'<option value="">Төхөөрөмж алга</option>';}
async function loadNotifications(){try{const rows=await api('/notifications');const unread=rows.filter(x=>!x.read_at).length;$('#notificationCount').hidden=unread===0;$('#notificationCount').textContent=unread>99?'99+':unread;if(currentRoute==='notifications')renderNotifications(rows);}catch(e){}}
function renderNotifications(rows){$('#notificationList').innerHTML=rows.length?rows.map(n=>`<div class="notification-item ${n.read_at?'':'unread'}"><span class="notification-dot ${esc(n.severity)}"></span><div class="notification-main"><b>${esc(n.title)}</b><small>${esc(n.message)}</small><small>${fmtRelative(n.created_at)}</small></div>${n.read_at?'<span class="muted">Уншсан</span>':`<button data-read-notification="${n.id}">Уншсан болгох</button>`}</div>`).join(''):emptyInline('Одоогоор шинэ мэдэгдэл алга.');}
async function loadUsers(){try{const rows=await api('/users');$('#usersRows').innerHTML=rows.map(x=>`<tr><td><div class="device-cell"><span class="avatar">${esc(initials(x.name))}</span><div><strong>${esc(x.name)}</strong><small>${esc(x.username)}</small></div></div></td><td>${esc(x.email)}</td><td><select class="role-select" data-user-role="${x.id}"><option value="student" ${x.role==='student'?'selected':''}>Оюутан</option><option value="staff" ${x.role==='staff'?'selected':''}>Багш / Лабораторийн ажилтан</option><option value="admin" ${x.role==='admin'?'selected':''}>Админ</option></select></td><td>${esc(x.organization||'—')}</td><td><span class="status-pill status-available">Идэвхтэй</span></td><td><span class="muted">ID ${x.id}</span></td></tr>`).join('');}catch(e){showPageError('users',e.message)}}
async function loadSettings(){try{const s=await api('/settings');$('#settingsForm').elements.default_loan_hours.value=s.default_loan_hours||24;$('#settingsForm').elements.low_stock_threshold.value=s.low_stock_threshold||2;$('#settingsForm').elements.prediction_horizon_days.value=s.prediction_horizon_days||7;$('#settingsForm').elements.notifications_enabled.checked=s.notifications_enabled!=='0';}catch(e){showPageError('settings',e.message)}}
function renderProfile(){if(!user)return;$('#profileFacts').innerHTML=[['И-мэйл',user.email],['Хэрэглэгчийн төрөл',roleLabel()],['Байгууллага',user.organization||'—'],['Хэрэглэгчийн нэр',user.username]].map(x=>`<div class="fact-row"><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join('');const f=$('#profileForm');f.elements.last_name.value=user.last_name||'';f.elements.first_name.value=user.first_name||'';f.elements.organization.value=user.organization||'';}
function openOnboarding(){onboardingIndex=0;renderOnboarding();openDialog('onboardingDialog')}
function renderOnboarding(){const items=[['Төхөөрөмжөө олоорой','Нэр, ангилал, байршлаар хайж хэрэгтэй төхөөрөмжөө хурдан олоорой.'],['QR кодоор аваарай','Төхөөрөмжийн QR кодыг уншуулж авах үйлдлийг хэдхэн алхмаар хийгээрэй.'],['Буцаах хугацаагаа хянаарай','Миний төхөөрөмжүүд хэсгээс буцаах хугацаа болон хэтэрсэн олголтоо хараарай.'],['Ирээдүйн хэрэгцээг хараарай','Ирэх 7 хоногт ямар төхөөрөмж илүү хэрэг болохыг урьдчилан хараарай.']];const x=items[onboardingIndex];$('#onboardingTitle').textContent=x[0];$('#onboardingText').textContent=x[1];$$('.onboarding-stepper span').forEach((s,i)=>s.classList.toggle('active',i<=onboardingIndex));$('#onboardingDialog [data-next-onboarding]').textContent=onboardingIndex===items.length-1?'Эхлэх →':'Дараагийнх →';}
async function finishOnboarding(){closeDialog('onboardingDialog');try{const result=await api('/profile','PUT',{first_name:user.first_name,last_name:user.last_name,organization:user.organization,onboarded:true});user={...user,...result,onboarded:true};setIdentity()}catch(e){toast(e.message,'error','Алдаа')}}

function prepareQr(){stopCamera();$('#qrManualCode').value='';$('#qrResult').innerHTML='<div class="empty-icon">▣</div><b>Төхөөрөмжөө сонгоно уу.</b><p>Төхөөрөмжийн QR кодыг уншуулж эсвэл дугаарыг гараар оруулна уу.</p>';setQrStep(0);$('#scannerMessage').textContent='Камер эхлүүлэхийг дарна уу';$('#startCamera').hidden=false;$('#stopCamera').hidden=true;}
function setQrStep(n){$$('#qrSteps .flow-step').forEach((s,i)=>s.classList.toggle('active',i<=n));}
async function lookupQr(code){code=(code||'').trim();if(!code){toast('Төхөөрөмжийн дугаарыг оруулна уу.','warning','Анхаарах');return;}try{const rows=await api('/equipment?q='+encodeURIComponent(code));const exact=rows.find(x=>x.code.toLowerCase()===code.toLowerCase())||rows[0];if(!exact)throw new Error('Төхөөрөмж олдсонгүй.');setQrStep(2);$('#qrResult').innerHTML=`<div class="result-device"><span class="status-pill ${exact.status==='available'?'status-available':'status-maintenance'}">${exact.status==='available'?'✓ Боломжтой':'⚠ Одоогоор боломжгүй'}</span><h3>${esc(exact.name)}</h3><p>${esc(exact.code)} · ${esc(exact.location)} · ${esc(exact.category)}</p><div class="result-device-actions">${exact.status==='available'?`<button class="primary-button" data-borrow-equipment-dialog="${exact.id}">Төхөөрөмж авах</button>`:''}<button class="secondary-button" data-detail-equipment="${exact.id}">Дэлгэрэнгүй</button></div></div>`;setQrStep(3)}catch(e){$('#qrResult').innerHTML=`<div class="empty-icon">!</div><b>Төхөөрөмж олдсонгүй.</b><p>${esc(e.message)}</p>`;setQrStep(0)}}
async function startCamera(){if(!navigator.mediaDevices?.getUserMedia){toast('Энэ браузер камер ашиглахыг дэмжихгүй байна. Дугаарыг гараар оруулаарай.','warning','Камер ашиглах боломжгүй');return;}if(!('BarcodeDetector'in window)){toast('QR уншигчийн browser дэмжлэг олдсонгүй. Дугаарыг гараар оруулаарай.','warning','Гараар оруулах');return;}try{cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});const video=$('#qrVideo');video.hidden=false;video.srcObject=cameraStream;await video.play();$('#startCamera').hidden=true;$('#stopCamera').hidden=false;$('#scannerMessage').textContent='QR кодоо хүрээнд тааруулна уу';const detector=new BarcodeDetector({formats:['qr_code']});const scan=async()=>{if(!cameraStream)return;try{const codes=await detector.detect(video);if(codes?.[0]?.rawValue){stopCamera();$('#qrManualCode').value=codes[0].rawValue;lookupQr(codes[0].rawValue);return;}}catch{}cameraTimer=requestAnimationFrame(scan)};scan()}catch(e){toast('Камерыг эхлүүлж чадсангүй. Browser-ийн зөвшөөрлөө шалгана уу.','error','Камерын алдаа')}}
function stopCamera(){if(cameraTimer)cancelAnimationFrame(cameraTimer);cameraTimer=null;if(cameraStream){cameraStream.getTracks().forEach(t=>t.stop());cameraStream=null;}const v=$('#qrVideo');if(v){v.pause?.();v.srcObject=null;v.hidden=true;}if($('#startCamera')){$('#startCamera').hidden=false;$('#stopCamera').hidden=true;}}
function updateStrength(value){let score=0;if(value.length>=8)score++;if(/[A-ZА-ЯӨҮЁ]/.test(value))score++;if(/[0-9]/.test(value))score++;if(/[^A-Za-zА-Яа-яӨөҮүЁё0-9]/.test(value))score++;const labels=['Сул','Сул','Дунд','Хүчтэй','Хүчтэй'];const widths=[8,25,55,78,100];$('#strengthBar').style.width=widths[score]+'%';$('#strengthLabel').textContent=value?`Нууц үг: ${labels[score]}`:'Нууц үгийн хүчийг шалгаж байна';}
function updatePublicStats(rows){$('#publicTotal').textContent=rows.length;$('#publicAvailable').textContent=rows.filter(x=>x.status==='available').length;$('#publicInUse').textContent=rows.filter(x=>x.status==='in_use').length;}
async function loadPublicEquipment(term=''){try{const rows=await api('/public/equipment?q='+encodeURIComponent(term));updatePublicStats(rows);$('#publicEquipmentGrid').innerHTML=rows.length?rows.map(x=>`<article class="public-card"><b>${esc(x.name)}</b><small>${esc(x.code)} · ${esc(x.category)} · ${esc(x.location)}</small>${statusPill(x.status)}</article>`).join(''):emptyInline('Одоогоор тохирох төхөөрөмж алга.')}catch{$('#publicEquipmentGrid').innerHTML=emptyInline('Мэдээллийг ачаалж чадсангүй.')}}

async function openRequestDecisionDialog(id,status){
  try{
    const row=(requests||[]).find(r=>r.id===id)|| (await api('/requests')).find(r=>r.id===id);
    if(!row)throw new Error('Төхөөрөмжийн хүсэлт олдсонгүй.');
    const approve=status==='approved';
    $('#requestDecisionForm').elements.request_id.value=id;
    $('#requestDecisionForm').elements.status.value=status;
    $('#requestDecisionTitle').textContent=approve?'Хүсэлтийг батлах':'Хүсэлтийг татгалзах';
    $('#requestDecisionSubtitle').textContent=approve?'Батлах тоо хэмжээг шалгаад шийдвэрээ баталгаажуулна уу.':'Шалтгааныг богино, ойлгомжтой байдлаар тэмдэглэнэ үү.';
    $('#requestDecisionFacts').innerHTML=[['Төхөөрөмж',row.equipment_name],['Хүссэн тоо',row.requested_qty],['Одоогийн нөөц',row.available_now],['Хичээл',`${row.course} · ${row.lab_no}`]].map(f=>`<div><small>${esc(f[0])}</small><b>${esc(f[1])}</b></div>`).join('');
    $('#requestDecisionForm').elements.approved_qty.value=Math.min(Math.max(1,Number(row.requested_qty||1)),Math.max(1,Number(row.requested_qty||1)));
    $('#approvedQtyField').hidden=!approve;
    $('#rejectionReasonField').hidden=approve;
    $('#requestDecisionForm').elements.reason.value='';
    $('#requestDecisionError').textContent='';
    $('#requestDecisionSubmit .button-label').textContent=approve?'Батлах':'Татгалзах';
    $('#requestDecisionSubmit').className='primary-button '+(approve?'':'danger-button');
    openDialog('requestDecisionDialog');
  }catch(err){toast(err.message,'error','Алдаа')}
}
async function openMaintenanceCompleteDialog(id,name){$('#maintenanceCompleteForm').elements.id.value=id;$('#maintenanceCompleteName').textContent=`${name} төхөөрөмжийн ажлын үр дүнг бүртгээд дуусгана.`;$('#maintenanceCompleteForm').elements.outcome.value='';$('#maintenanceCompleteError').textContent='';openDialog('maintenanceCompleteDialog');}

// Events
addEventListener('hashchange',()=>{if(user)routeTo(location.hash.slice(1)||'dashboard',false)});
addEventListener('popstate',()=>{if(user)routeTo(location.hash.slice(1)||'dashboard',false)});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button,a');if(!b)return;
  if(b.dataset.publicCatalog!==undefined){openDialog('publicCatalog');loadPublicEquipment();return;}
  if(b.dataset.publicHome!==undefined){e.preventDefault();showWelcome();return;}
  if(b.dataset.showLogin!==undefined){showAuth('login');return;}
  if(b.dataset.openRegister!==undefined){showAuth('register');return;}
  if(b.dataset.showWelcome!==undefined){showWelcome();return;}
  if(b.dataset.forgotPassword!==undefined){$('#forgotForm').reset();$('#forgotMessage').textContent='';openDialog('forgotDialog');return;}
  if(b.dataset.nav){e.preventDefault();routeTo(b.dataset.nav);return;}
  if(b.dataset.closeDialog!==undefined){b.closest('dialog')?.close();return;}
  if(b.dataset.closeSidebar!==undefined){closeSidebar();return;}
  if(b.dataset.openSidebar!==undefined){openSidebar();return;}
  if(b.dataset.togglePassword){const input=$('#'+b.dataset.togglePassword);input.type=input.type==='password'?'text':'password';b.setAttribute('aria-label',input.type==='password'?'Нууц үгийг нуух':'Нууц үгийг харах');return;}
  if(b.dataset.addEquipmentInline!==undefined){openEquipmentEditor();return;}
  if(b.id==='addEquipment'){openEquipmentEditor();return;}
  if(b.dataset.detailEquipment){openEquipmentDetail(Number(b.dataset.detailEquipment));return;}
  if(b.dataset.editEquipment){openEquipmentEditor(Number(b.dataset.editEquipment));return;}
  if(b.dataset.editFromDetail){closeDialog('equipmentDetailDialog');openEquipmentEditor(Number(b.dataset.editFromDetail));return;}
  if(b.dataset.borrowEquipment){openBorrowDialog(Number(b.dataset.borrowEquipment));return;}
  if(b.dataset.borrowEquipmentDialog){closeDialog('equipmentDetailDialog');openBorrowDialog(Number(b.dataset.borrowEquipmentDialog),Number(b.dataset.requestId||0));return;}
  if(b.dataset.borrowRequest!==undefined){return;}
  if(b.dataset.returnLoan){openReturnDialog(Number(b.dataset.returnLoan),b.dataset.returnName);return;}
  if(b.dataset.openRequest!==undefined){openRequestDialog(b.dataset.requestDevice||'');return;}
  if(b.dataset.requestCheckout){const name=b.dataset.equipmentName;const matches=await api('/equipment?q='+encodeURIComponent(name)+'&status=available');if(matches.length){openBorrowDialog(matches[0].id,Number(b.dataset.requestCheckout));}else{toast('Энэ төхөөрөмж одоогоор боломжгүй байна.','warning','Анхаарах')}return;}
  if(b.dataset.requestStatus){openRequestDecisionDialog(Number(b.dataset.requestStatus), b.dataset.status);return;}
  if(b.dataset.recStatus){try{await api('/recommendations/'+b.dataset.recStatus+'/status','POST',{status:b.dataset.status});toast(b.dataset.status==='accepted'?'Зөвлөмжийг хүлээн авлаа.':'Зөвлөмжийг хаалаа.');await loadRecommendations()}catch(err){toast(err.message,'error','Алдаа')}return;}
  if(b.dataset.readNotification){try{await api('/notifications/'+b.dataset.readNotification+'/read','POST',{});await loadNotifications();}catch(err){toast(err.message,'error','Алдаа')}return;}
  if(b.dataset.retryPage){routeTo(b.dataset.retryPage,false);return;}
  if(b.dataset.nextOnboarding!==undefined){if(onboardingIndex<3){onboardingIndex++;renderOnboarding()}else{finishOnboarding()}return;}
  if(b.dataset.skipOnboarding!==undefined){finishOnboarding();return;}
  if(b.id==='markAllRead'){try{await api('/notifications/read-all','POST',{});await loadNotifications();renderNotifications(await api('/notifications'));toast('Бүх мэдэгдлийг уншсан болголоо.')}catch(err){toast(err.message,'error','Алдаа')}return;}
  if(b.id==='startCamera'){startCamera();return;}
  if(b.id==='stopCamera'){stopCamera();return;}
  if(b.id==='lookupQr'){lookupQr($('#qrManualCode').value);return;}
  if(b.id==='resetEquipmentFilters'){clearEquipmentFilters();return;}
  if(b.id==='exportReport'){window.location.href='/api/reports/export';return;}
  if(b.id==='addMaintenance'){createMaintenance();return;}
  if(b.dataset.finishMaintenance){openMaintenanceCompleteDialog(Number(b.dataset.finishMaintenance), b.closest('.maintenance-row')?.querySelector('b')?.textContent||'Төхөөрөмж');return;}
  if(b.dataset.tab){$$('.tab').forEach(t=>t.classList.toggle('active',t===b));$$('.tab-panel').forEach(p=>p.hidden=p.id!==b.dataset.tab);return;}
  if(b.dataset.toggleAdvanced){const target=$('#'+b.dataset.toggleAdvanced);target.hidden=!target.hidden;b.querySelector('b').textContent=target.hidden?'＋':'−';return;}
  if(b.dataset.runMl!==undefined){try{b.disabled=true;b.textContent='Таамаглаж байна...';const r=await api('/ml/run','POST',{});toast(`Шинэ таамаг ${r.predictions||0} мөрөөр хадгалагдлаа.`);await Promise.all([loadDemand(),loadRecommendations(),loadNotifications(),loadDashboard()])}catch(err){toast(err.message,'error','Алдаа')}finally{b.disabled=false;b.textContent='Таамгийг шинэчлэх'}return;}
});

$('#loginForm').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#loginSubmit');$('#loginError').textContent='';setBusy(btn,true,'Нэвтэрч байна...');try{const f=new FormData(e.target);const result=await api('/login','POST',{email:f.get('email'),password:f.get('password')});e.target.reset();$('#loginNotice').textContent='';await enter(result)}catch(err){$('#loginError').textContent=err.message}finally{setBusy(btn,false)}});
$('#registerForm').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#registerSubmit');$('#registerError').textContent='';const f=new FormData(e.target);if(f.get('password')!==f.get('confirm')){$('#registerError').textContent='Нууц үгүүд тохирохгүй байна.';return;}setBusy(btn,true,'Бүртгэл үүсгэж байна...');try{const result=await api('/register','POST',Object.fromEntries(f));toggleAuthMode('login');$('#loginForm').elements.email.value=result.username;$('#loginNotice').textContent='Бүртгэл амжилттай үүслээ. И-мэйл эсвэл хэрэглэгчийн нэрээрээ нэвтэрнэ үү.';$('#loginForm').elements.password.focus()}catch(err){$('#registerError').textContent=err.message}finally{setBusy(btn,false)}});
$('#registerPassword').addEventListener('input',e=>updateStrength(e.target.value));
$('#equipmentForm').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#equipmentSave');$('#equipmentError').textContent='';setBusy(btn,true,'Хадгалж байна...');try{const data=Object.fromEntries(new FormData(e.target));const id=data.id;delete data.id;await api('/equipment'+(id?'/'+id:''),id?'PUT':'POST',data);closeDialog('equipmentDialog');toast(id?'Төхөөрөмж шинэчлэгдлээ.':'Төхөөрөмж нэмэгдлээ.');await Promise.all([loadEquipment(),loadDashboard()])}catch(err){$('#equipmentError').textContent=err.message}finally{setBusy(btn,false)}});
$('#borrowForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;$('#borrowError').textContent='';setBusy(btn,true,'Баталгаажуулж байна...');try{await api('/loans','POST',{equipment_id:Number(e.target.dataset.equipmentId),due_at:new Date($('#dueAt').value).toISOString(),request_id:Number(e.target.dataset.requestId||0)});closeDialog('borrowDialog');toast('Төхөөрөмж амжилттай олгогдлоо.');await Promise.all([loadEquipment(),loadLoans(),loadRequests(),loadDashboard(),loadNotifications()])}catch(err){$('#borrowError').textContent=err.message}finally{setBusy(btn,false)}});
$('#returnForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;$('#returnError').textContent='';setBusy(btn,true,'Буцааж байна...');try{await api('/loans/'+e.target.elements.loan_id.value+'/return','POST',{return_condition:e.target.elements.return_condition.value});closeDialog('returnDialog');toast('Төхөөрөмж амжилттай буцаагдлаа.');await Promise.all([loadEquipment(),loadLoans(),loadDashboard(),loadNotifications()]);if(currentRoute==='qr')prepareQr()}catch(err){$('#returnError').textContent=err.message}finally{setBusy(btn,false)}});
$('#requestForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;$('#requestError').textContent='';setBusy(btn,true,'Илгээж байна...');try{const f=new FormData(e.target);await api('/requests','POST',{session_id:Number(f.get('session_id')),equipment_name:f.get('equipment_name'),requested_qty:Number(f.get('requested_qty'))});closeDialog('requestDialog');toast('Хүсэлт амжилттай илгээгдлээ.');await Promise.all([loadRequests(),loadNotifications()])}catch(err){$('#requestError').textContent=err.message}finally{setBusy(btn,false)}});
$('#maintenanceForm').addEventListener('submit',async e=>{e.preventDefault();$('#maintenanceError').textContent='';try{const f=new FormData(e.target);const scheduled=f.get('scheduled_at');await api('/maintenance','POST',{equipment_id:Number(f.get('equipment_id')),issue:f.get('issue'),priority:f.get('priority'),scheduled_at:scheduled?new Date(scheduled).toISOString():''});e.target.reset();$('#maintenanceFormPanel').hidden=true;toast('Засварын ажил бүртгэгдлээ.');await Promise.all([loadMaintenance(),loadEquipment(),loadDashboard(),loadNotifications()])}catch(err){$('#maintenanceError').textContent=err.message}});
$('#requestDecisionForm').addEventListener('submit',async e=>{e.preventDefault();const btn=$('#requestDecisionSubmit');$('#requestDecisionError').textContent='';setBusy(btn,true);try{const f=new FormData(e.target);const status=f.get('status');const payload={status,approved_qty:status==='approved'?Number(f.get('approved_qty')):0,reason:f.get('reason')||''};await api('/requests/'+f.get('request_id')+'/status','POST',payload);closeDialog('requestDecisionDialog');toast(status==='approved'?'Хүсэлтийг баталлаа.':'Хүсэлтийг татгалзлаа.',status==='approved'?'success':'warning',status==='approved'?'Амжилттай':'Анхаарах');await Promise.all([loadRequests(),loadNotifications(),loadDashboard()])}catch(err){$('#requestDecisionError').textContent=err.message}finally{setBusy(btn,false)}});
$('#maintenanceCompleteForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;$('#maintenanceCompleteError').textContent='';setBusy(btn,true,'Хадгалж байна...');try{const f=new FormData(e.target);await api('/maintenance/'+f.get('id'),'PUT',{outcome:f.get('outcome')});closeDialog('maintenanceCompleteDialog');toast('Засвар дууссан гэж бүртгэлээ.');await Promise.all([loadMaintenance(),loadEquipment(),loadDashboard(),loadNotifications()])}catch(err){$('#maintenanceCompleteError').textContent=err.message}finally{setBusy(btn,false)}});

$('#profileForm').addEventListener('submit',async e=>{e.preventDefault();try{const f=Object.fromEntries(new FormData(e.target));const result=await api('/profile','PUT',f);user={...user,...result};setIdentity();$('#profileMessage').textContent='Мэдээлэл хадгалагдлаа.';toast('Профайлыг шинэчиллээ.')}catch(err){$('#profileMessage').textContent=err.message}});
$('#settingsForm').addEventListener('submit',async e=>{e.preventDefault();try{const f=new FormData(e.target);await api('/settings','PUT',{default_loan_hours:Number(f.get('default_loan_hours')),low_stock_threshold:Number(f.get('low_stock_threshold')),prediction_horizon_days:Number(f.get('prediction_horizon_days')),notifications_enabled:f.get('notifications_enabled')==='on'});$('#settingsMessage').textContent='Тохиргоо хадгалагдлаа.';toast('Тохиргоог шинэчиллээ.')}catch(err){$('#settingsMessage').textContent=err.message}});
$('#forgotForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter;$('#forgotMessage').textContent='';setBusy(btn,true,'Илгээж байна...');try{const f=new FormData(e.target);const result=await api('/password-reset','POST',{email:f.get('email')});$('#forgotMessage').textContent=result.message||'Хүсэлтийг хүлээн авлаа.';toast('Сэргээх хүсэлтийг хүлээн авлаа.','success','Амжилттай');setTimeout(()=>closeDialog('forgotDialog'),900)}catch(err){$('#forgotMessage').textContent=err.message}finally{setBusy(btn,false)}});
$('#logout').addEventListener('click',async()=>{try{await api('/logout','POST',{})}catch{}logoutLocally()});
$('#equipmentSearch').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadEquipment,250)});['equipmentCategory','equipmentLocation','equipmentStatus'].forEach(id=>$('#'+id).addEventListener('change',loadEquipment));
$('#publicSearch').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadPublicEquipment($('#publicSearch').value),220)});
document.addEventListener('change',async e=>{const sel=e.target.closest('[data-user-role]');if(!sel)return;try{await api('/users/'+sel.dataset.userRole+'/role','POST',{role:sel.value});toast('Хэрэглэгчийн эрх шинэчлэгдлээ.');await loadUsers()}catch(err){toast(err.message,'error','Алдаа')}});
function openSidebar(){ $('#sidebar').classList.add('open');$('#sidebarBackdrop').style.display='block'; }
function closeSidebar(){ $('#sidebar').classList.remove('open');$('#sidebarBackdrop').style.display=''; }

(async function bootstrap(){try{const me=await api('/me');await enter(me)}catch{showWelcome();loadPublicEquipment()}})();
