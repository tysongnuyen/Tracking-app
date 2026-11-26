'use strict';
const el = id => document.getElementById(id);
async function init() {
  const saved = await chrome.storage.local.get(['client', 'token']);
  el('client').value = saved.client || (/Edg\//.test(navigator.userAgent) ? 'msedge' : 'chrome');
  el('token').value = saved.token || '';
  const session = await chrome.storage.session.get(['status']);
  el('status').textContent = session.status || 'Chưa thiết lập kết nối.';
}
el('connect').addEventListener('click', async () => {
  const token = el('token').value.trim();
  if (!/^[a-f0-9]{64}$/.test(token)) { el('status').textContent = 'Mã cần gồm 64 ký tự. Hãy sao chép lại từ Cài đặt của app.'; return; }
  el('connect').disabled = true;
  el('status').textContent = 'Đang kết nối…';
  try {
    await chrome.storage.local.set({client: el('client').value, token});
    await chrome.runtime.sendMessage({type: 'connect'});
    const session = await chrome.storage.session.get(['status']);
    el('status').textContent = session.status || 'Chưa có phản hồi.';
  } catch { el('status').textContent = 'Chưa kết nối. Hãy mở app rồi thử lại.'; }
  finally { el('connect').disabled = false; }
});
init();
