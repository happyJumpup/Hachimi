const app = document.getElementById('app');
const healthStatus = document.getElementById('healthStatus');

let sessionId = null;
let lastPayload = null;
let healthText = '读取题库中';
let pendingAnswer = null;
let userProfile = { heightCm: '', weightKg: '', sex: '', skipped: false };
let pendingResultPayload = null;
let requestInFlight = false;
let selectedPetChoiceIndex = 0;
let petChoiceLocked = false;

async function request(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload || {})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

async function loadHealth() {
  try {
    const response = await fetch('/api/health');
    const data = await response.json();
    healthText = `${data.questionCount} 道题 · 已准备`;
    healthStatus.textContent = healthText;
    updateInlineHealth();
  } catch {
    healthText = '题库读取失败';
    healthStatus.textContent = healthText;
    updateInlineHealth();
  }
}

function renderIntro() {
  app.innerHTML = drawerFrame({
    action: 'GYMTI 01',
    title: '欢迎来到 GYMTI 健身人格测试',
    body: `
      <div class="welcome-copy">
        <p>完成几道轻松小题，我们会帮你了解自己的健身人格，并匹配一位最适合你的哈基米健身教练。</p>
        <p>它可能会温柔陪跑、专业分析、热血鼓励，也可能在你想摆烂时轻轻推你一把。</p>
        <p>准备好了吗？快来了解你的健身人格，领取你的专属健身哈基米吧。</p>
      </div>
    `,
    footer: `
      <button class="done-button" type="button" data-action="start">开始测试</button>
    `
  });
}

function renderProfileStep() {
  app.innerHTML = drawerFrame({
    action: 'PERSONAL 01',
    title: '个性化负荷信息',
    body: `
      <div class="notice">以下信息可以跳过。填写后仅用于个性化健身负荷生成和训练建议，不会影响你的测评结果。</div>

      <div class="profile-grid">
        <label class="profile-field">
          <span>您的身高</span>
          <div class="profile-input">
            <input id="heightInput" type="number" inputmode="decimal" min="80" max="230" placeholder="留空" value="${escapeHtml(userProfile.heightCm)}" />
            <em>cm</em>
          </div>
        </label>
        <label class="profile-field">
          <span>您的体重</span>
          <div class="profile-input">
            <input id="weightInput" type="number" inputmode="decimal" min="20" max="250" step="0.1" placeholder="留空" value="${escapeHtml(userProfile.weightKg)}" />
            <em>kg</em>
          </div>
        </label>
      </div>

      <label class="field-label">您的性别</label>
      <div class="sex-options" aria-label="性别，可跳过">
        ${renderSexOption('female', '女')}
        ${renderSexOption('male', '男')}
        ${renderSexOption('other', '其他')}
        ${renderSexOption('prefer_not_to_say', '暂不说明')}
      </div>

      <section class="source-box">
        <p>SOURCE</p>
        <strong>可跳过 · 仅用于建议生成</strong>
        <span>如果你选择跳过，系统会使用保守默认负荷，并在后续训练反馈中慢慢校准。</span>
      </section>
    `,
    footer: `
      <button class="ghost-button" type="button" data-action="skip-profile">跳过</button>
      <button class="outline-button danger" type="button" data-action="clear-profile">清空</button>
      <button class="done-button" type="button" data-action="submit-profile">完成</button>
    `
  });
}

function renderSexOption(value, label) {
  return `
    <button class="${userProfile.sex === value ? 'sex-option selected' : 'sex-option'}" type="button" data-action="select-sex" data-sex="${value}">
      ${escapeHtml(label)}
    </button>
  `;
}

function drawerFrame({ action, title, body, footer }) {
  return `
    <div class="drawer-inner">
      <header class="drawer-header">
        <div>
          <p class="action-label">${escapeHtml(action)}</p>
          <h1>${escapeHtml(title)}</h1>
        </div>
        <button class="close-button" type="button" data-action="reset" aria-label="关闭">×</button>
      </header>
      <section class="drawer-body">${body}</section>
      <footer class="drawer-footer">${footer}</footer>
    </div>
  `;
}

function setLoading(text) {
  app.innerHTML = drawerFrame({
    action: 'LOADING',
    title: text,
    body: `
      <div class="notice">正在整理你的回答，马上进入下一步。</div>
      <div class="loading-bars">
        <span></span><span></span><span></span>
      </div>
    `,
    footer: '<button class="ghost-button" type="button" data-action="reset">回到开始</button>'
  });
}

async function startQuiz() {
  if (requestInFlight) return;
  try {
    requestInFlight = true;
    pendingAnswer = null;
    pendingResultPayload = null;
    selectedPetChoiceIndex = 0;
    petChoiceLocked = false;
    userProfile = { heightCm: '', weightKg: '', sex: '', skipped: false };
    setLoading('正在生成第一题');
    const payload = await request('/api/start');
    sessionId = payload.sessionId;
    lastPayload = payload;
    renderPayload(payload);
  } catch (error) {
    renderError(error);
  } finally {
    requestInFlight = false;
  }
}

async function submitProfile({ skipped = false } = {}) {
  if (requestInFlight) return;
  try {
    requestInFlight = true;
    collectProfileFromForm({ skipped });
    document.querySelectorAll('button[data-action="skip-profile"], button[data-action="submit-profile"]').forEach((button) => {
      button.disabled = true;
    });
    setLoading('正在生成结果');
    const payload = await request('/api/profile', { sessionId, profile: userProfile });
    pendingResultPayload = null;
    lastPayload = payload;
    selectedPetChoiceIndex = 0;
    petChoiceLocked = false;
    renderResult(payload);
  } catch (error) {
    renderError(error);
  } finally {
    requestInFlight = false;
  }
}

async function answerQuestion(questionId, optionId) {
  if (requestInFlight) return;
  try {
    requestInFlight = true;
    disableOptionButtons();
    const submit = document.querySelector('button[data-action="submit-answer"]');
    if (submit) submit.disabled = true;
    const payload = await request('/api/answer', { sessionId, questionId, optionId });
    lastPayload = payload;
    renderPayload(payload);
  } catch (error) {
    renderError(error);
  } finally {
    requestInFlight = false;
  }
}

async function finishNow() {
  if (requestInFlight) return;
  try {
    requestInFlight = true;
    setLoading('正在提前生成结果');
    const payload = await request('/api/finish', { sessionId });
    lastPayload = payload;
    renderPayload(payload);
  } catch (error) {
    renderError(error);
  } finally {
    requestInFlight = false;
  }
}

function renderPayload(payload) {
  if (payload.done) {
    pendingResultPayload = payload;
    renderProfileStep();
  } else {
    renderQuestion(payload);
  }
}

function renderQuestion(payload) {
  const question = payload.question;
  const answeredNext = payload.answeredCount + 1;
  const progress = Math.min(100, Math.round((payload.answeredCount / payload.maxQuestions) * 100));
  const options = question.options.map((option) => `
    <button class="answer-row" type="button" data-action="select-answer" data-question="${question.id}" data-option="${option.id}">
      <span class="${option.noMatch ? 'answer-letter wide' : 'answer-letter'}">${escapeHtml(option.displayId || option.id)}</span>
      <span>${escapeHtml(option.label)}</span>
    </button>
  `).join('');
  pendingAnswer = null;

  app.innerHTML = drawerFrame({
    action: `QUESTION ${String(answeredNext).padStart(2, '0')}`,
    title: question.text,
    body: `
      <div class="progress-wrap">
        <span>已答 ${payload.answeredCount} / ${payload.maxQuestions}</span>
        <div class="progress-track"><div style="width:${progress}%"></div></div>
      </div>

      <label class="field-label">选择最像你的反应</label>
      <div class="answers">${options}</div>
    `,
    footer: `
      <button class="ghost-button" type="button" data-action="reset">重新开始</button>
      ${payload.canFinish ? '<button class="outline-button danger" type="button" data-action="finish">提前结果</button>' : '<button class="outline-button danger muted-action" type="button" disabled>继续答</button>'}
      <button class="done-button" type="button" data-action="submit-answer" disabled>完成</button>
    `
  });
}

function renderResult(payload) {
  const { result, narrative } = payload;
  const profileContext = payload.profileContext || {};
  const petChoices = payload.petChoices || [];
  if (selectedPetChoiceIndex >= petChoices.length) selectedPetChoiceIndex = 0;
  const selectedPet = petChoices[selectedPetChoiceIndex] || {
    type: result.pet,
    oneLiner: result.petOneLiner,
    voice: result.petVoice
  };
  const selectedPetType = selectedPet.type || result.pet;
  const selectedPetIsInitial = selectedPetType === result.pet;
  const petAvatar = selectedPetType && selectedPetType !== '待确认猫咪'
    ? `<img src="/avatar?type=${encodeURIComponent(selectedPetType)}" alt="${escapeHtml(selectedPetType)}">`
    : '<span>暂无头像</span>';
  const gymtiImage = result.gymti && result.gymti !== 'UNSET'
    ? `<div class="gymti-visual"><img src="/gymti-image?type=${encodeURIComponent(result.gymti)}" alt="${escapeHtml(result.gymtiDisplay)} ${escapeHtml(result.gymtiName)}"></div>`
    : '';
  const summary = selectedPetIsInitial
    ? (narrative.summary || result.gymtiOneLiner)
    : `${result.gymtiOneLiner} 人格结果保持不变，当前为你试配“${selectedPetType}”哈基米教练。`;
  const actions = (narrative.nextActions || result.gymtiAdvice || [])
    .slice(0, 3)
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join('');
  const petLines = selectedPetIsInitial ? (narrative.petLines || []) : petLinesFor(selectedPetType);
  const lines = petLines
    .slice(0, 2)
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join('');
  const choiceHint = petChoices.length > 1
    ? `<p class="subline">候选 ${selectedPetChoiceIndex + 1} / ${petChoices.length} · 人格结果保持不变</p>`
    : '';
  const choiceControls = petChoices.length > 1
    ? `<div class="pet-choice-controls">
        <button type="button" data-action="switch-pet" ${petChoiceLocked ? 'disabled' : ''}>换一只哈基米</button>
        <button type="button" data-action="lock-pet" class="${petChoiceLocked ? 'selected' : ''}">${petChoiceLocked ? '已选定' : '就选这只'}</button>
      </div>`
    : '';

  app.innerHTML = drawerFrame({
    action: `RESULT · ${payload.answeredCount} 题`,
    title: selectedPetIsInitial
      ? (narrative.headline || `${result.gymtiDisplay} ${result.gymtiName} × ${selectedPetType}`)
      : `${result.gymtiDisplay} ${result.gymtiName} × ${selectedPetType}`,
    body: `
      <div class="result-hero">
        <div class="cat-frame">${petAvatar}</div>
        <div>
          <p class="field-label">你的猫咪桌宠</p>
          <h2>${escapeHtml(selectedPetType)}</h2>
          ${choiceHint}
        </div>
      </div>

      ${choiceControls}

      <div class="notice">${escapeHtml(summary)}</div>

      <label class="field-label">GYMTI</label>
      <div class="input-card gymti-result-card">
        ${gymtiImage}
        <strong>${escapeHtml(result.gymtiDisplay)} · ${escapeHtml(result.gymtiName)}</strong>
        <span>${escapeHtml(narrative.gymtiRead || result.gymtiOneLiner)}</span>
        ${result.gymtiSecondary ? `<em>副人格倾向：${escapeHtml(result.gymtiSecondary)} ${escapeHtml(result.gymtiSecondaryName || '')}</em>` : ''}
      </div>

      <label class="field-label">猫咪语气</label>
      <div class="input-card">
        <strong>${escapeHtml(selectedPetType)}</strong>
        <span>${escapeHtml(selectedPet.voice || narrative.petRead || result.petVoice)}</span>
        <em>${escapeHtml(selectedPet.oneLiner || result.petOneLiner)}</em>
      </div>

      <label class="field-label">个性化负荷信息</label>
      <div class="input-card">
        <strong>${escapeHtml(profileContext.summary || '未填写个人信息')}</strong>
        <span>${escapeHtml(profileContext.usage || '个人信息仅用于个性化健身负荷生成和训练建议。')}</span>
        <em>${escapeHtml(profileContext.advice || '')}</em>
      </div>

      <div class="chip-row">${actions}</div>

      <section class="source-box">
        <p>CAT LINES</p>
        <ul>${lines}</ul>
      </section>

    `,
    footer: `
      <button class="ghost-button" type="button" data-action="copy-result">复制结果</button>
      <button class="outline-button danger" type="button" data-action="reset">再测一次</button>
      <button class="done-button" type="button" data-action="start">重新开始</button>
    `
  });
}

function renderRanks(items) {
  return `
    <ol class="rank-list">
      ${(items || []).slice(0, 4).map((item) => `
        <li><span>${escapeHtml(item.key)}</span><strong>${escapeHtml(String(item.value))}</strong></li>
      `).join('')}
    </ol>
  `;
}

function petLinesFor(type) {
  return {
    热血鼓励型: ['状态可以慢慢来，但气势先给我上线。', '今天不需要完美，先把第一组点燃。'],
    温柔陪伴型: ['不用急，我陪你从最轻的一步开始。', '今天能回来，就已经很值得夸。'],
    毒舌督促型: ['借口先放旁边，本喵只看你下一组动不动。', '可以慢，但别原地假装加载。'],
    专业数据型: ['本次目标先锁定动作质量，再看完成度。', '建议从保守负荷开始，用反馈校准下一次。'],
    幽默话痨型: ['训练先开机，脑内弹幕本喵承包。', '你负责动起来，我负责让它没那么无聊。'],
    挑战突破型: ['别想太多，先完成这一组再谈退路。', '临门一脚了，本喵推你一把。'],
    佛系陪练型: ['今天不拼命，先让身体恢复连接。', '低压力启动也算启动，慢慢来。']
  }[type] || ['这只哈基米已就位，等你开始训练。', '先试试看，感觉不合适还能再换。'];
}

function currentPetTypeForCopy() {
  const choices = lastPayload?.petChoices || [];
  return choices[selectedPetChoiceIndex]?.type || lastPayload?.result?.pet || '';
}

function renderError(error) {
  app.innerHTML = drawerFrame({
    action: 'ERROR',
    title: '程序遇到问题',
    body: `<div class="error-box">${escapeHtml(error.message || String(error))}</div>`,
    footer: '<button class="done-button" type="button" data-action="reset">回到开始</button>'
  });
}

function disableOptionButtons() {
  document.querySelectorAll('.answer-row').forEach((button) => {
    button.disabled = true;
  });
}

function selectPendingAnswer(button) {
  pendingAnswer = {
    questionId: button.dataset.question,
    optionId: button.dataset.option
  };
  document.querySelectorAll('.answer-row').forEach((item) => item.classList.remove('selected'));
  button.classList.add('selected');
  const submit = document.querySelector('button[data-action="submit-answer"]');
  if (submit) submit.disabled = false;
}

function submitPendingAnswer() {
  if (!pendingAnswer) return;
  const answer = pendingAnswer;
  pendingAnswer = null;
  answerQuestion(answer.questionId, answer.optionId);
}

function collectProfileFromForm({ skipped = false } = {}) {
  const heightInput = document.getElementById('heightInput');
  const weightInput = document.getElementById('weightInput');
  if (heightInput) userProfile.heightCm = heightInput.value.trim();
  if (weightInput) userProfile.weightKg = weightInput.value.trim();
  userProfile.skipped = skipped;
  if (skipped) {
    userProfile = { heightCm: '', weightKg: '', sex: '', skipped: true };
  }
}

function clearProfileForm() {
  userProfile = { heightCm: '', weightKg: '', sex: '', skipped: false };
  renderProfileStep();
}

function selectSex(button) {
  userProfile.sex = button.dataset.sex || '';
  document.querySelectorAll('.sex-option').forEach((item) => item.classList.remove('selected'));
  button.classList.add('selected');
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement('textarea');
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
}

function updateInlineHealth() {
  document.querySelectorAll('.inline-health').forEach((item) => {
    item.textContent = healthText;
  });
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

app.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const { action } = button.dataset;
  if (action === 'profile') renderProfileStep();
  if (action === 'start') startQuiz();
  if (action === 'reset') {
    sessionId = null;
    lastPayload = null;
    pendingAnswer = null;
    pendingResultPayload = null;
    selectedPetChoiceIndex = 0;
    petChoiceLocked = false;
    userProfile = { heightCm: '', weightKg: '', sex: '', skipped: false };
    renderIntro();
  }
  if (action === 'skip-profile') submitProfile({ skipped: true });
  if (action === 'submit-profile') submitProfile();
  if (action === 'clear-profile') clearProfileForm();
  if (action === 'select-sex') selectSex(button);
  if (action === 'finish') finishNow();
  if (action === 'select-answer') selectPendingAnswer(button);
  if (action === 'submit-answer') submitPendingAnswer();
  if (action === 'switch-pet' && lastPayload?.done && !petChoiceLocked) {
    const choices = lastPayload.petChoices || [];
    if (choices.length > 1) {
      selectedPetChoiceIndex = (selectedPetChoiceIndex + 1) % choices.length;
      renderResult(lastPayload);
    }
  }
  if (action === 'lock-pet' && lastPayload?.done) {
    petChoiceLocked = true;
    renderResult(lastPayload);
  }
  if (action === 'copy-rules') {
    copyText('一次轻松答题，同时找到你的健身倾向和最合适的猫咪桌宠语气。');
  }
  if (action === 'copy-result' && lastPayload?.done) {
    copyText(`${lastPayload.result.gymtiDisplay} ${lastPayload.result.gymtiName} × ${currentPetTypeForCopy()}`);
  }
});

renderIntro();
loadHealth();
