// Общие функции и подписи для экранов клиента и сотрудника

const STATUS = {draft:"Черновик клиента", submitted:"Отправлен", in_review:"На проверке",
  docs_requested:"Запрошены документы", unblocked:"Разблокирован", rejected:"Отказ"};
const REASON = {income_source:"Подтвердить источник дохода", transit:"Транзитные операции",
  cash_out:"Снятие наличных", p2p_volume:"Много переводов от физлиц"};

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const rub = v => (v == null ? "—" : Number(v).toLocaleString("ru-RU", {maximumFractionDigits: 0}) + " ₽");
const dt = v => v ? new Date(v).toLocaleString("ru-RU", {day:"2-digit", month:"2-digit", year:"numeric", hour:"2-digit", minute:"2-digit"}) : "—";
const d = v => v ? new Date(v).toLocaleDateString("ru-RU") : "—";

function toast(msg){
  const t = document.getElementById("toast");
  t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.style.display = "none", 3500);
}

// запрос к API; ошибка FastAPI превращается в читаемый текст
async function api(path, opts = {}){
  const headers = opts.body instanceof FormData ? {} : {"Content-Type": "application/json"};
  const r = await fetch(path, {headers, ...opts});
  const body = await r.json().catch(() => ({}));
  if (!r.ok){
    const det = body.detail;
    if (typeof det === "string") throw new Error(det);
    if (det && det.missing) throw new Error(det.message + ": " + det.missing.join("; "));
    if (Array.isArray(det)) throw new Error(det.map(x => x.msg).join("; "));
    throw new Error(JSON.stringify(det || body));
  }
  return body;
}

// лента переписки; mine — чьи сообщения справа (client на экране клиента, staff на экране сотрудника)
function renderChat(el, items, mine){
  const WHO = {client: "Клиент", staff: "Сотрудник банка", system: "Система"};
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = items.length ? items.map(m => {
    const cls = m.from === "system" ? "system" : (m.from === mine ? "mine" : "theirs");
    const list = m.items && m.items.length ? `<ul>${m.items.map(i => `<li>${esc(i)}</li>`).join("")}</ul>` : "";
    const who = cls === "theirs" ? `<b class="who">${WHO[m.from] || esc(m.from)}</b>` : "";
    return `<div class="msg ${cls}">${who}${esc(m.text)}${list}<time>${dt(m.at)}</time></div>`;
  }).join("") : '<div class="empty">Сообщений пока нет</div>';
  if (atBottom) el.scrollTop = el.scrollHeight;
}

// пользователь сейчас печатает — не перерисовываем формы под ним
const isTyping = () => ["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement?.tagName);
