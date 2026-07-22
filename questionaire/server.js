const http = require('http');
const fs = require('fs');
const fsp = require('fs/promises');
const path = require('path');
const crypto = require('crypto');
const {
  parseMarkdown,
  summarizeState,
  shouldFinish,
  questionToPublic,
  chooseNextHeuristic,
  buildFinalResult,
  publicCandidate,
  PET_TYPES,
  PET_AVATARS,
  NONE_OPTION_ID
} = require('./questionnaire');

loadEnvFile(path.join(__dirname, '.env'));

const PORT = Number(process.env.PORT || 5178);
const MIN_QUESTIONS = Number(process.env.MIN_QUESTIONS || 5);
const MAX_QUESTIONS = Number(process.env.MAX_QUESTIONS || 8);
const DEFAULT_QUESTIONNAIRE_PATH = path.join(__dirname, 'data', 'GYMTI与桌宠联合问卷设计 1.md');
const QUESTIONNAIRE_PATH = resolveQuestionnairePath(process.env.GYMTI_QUESTIONNAIRE_PATH, DEFAULT_QUESTIONNAIRE_PATH);
const CAT_AVATAR_DIR = process.env.CAT_AVATAR_DIR || path.join(__dirname, 'assets', 'cat-fitness-avatars');
const GYMTI_IMAGE_DIR = process.env.GYMTI_IMAGE_DIR || path.join(__dirname, 'assets', 'GYMTI-images');
const PUBLIC_DIR = path.join(__dirname, 'public');
const OPENING_QUESTION_ID = 'q5';
const OPENING_QUESTION_PAT = /健身房天花板突然裂开|全知全能的健身神/;
const GYMTI_IMAGES = {
  IMNB: 'GYMTI-IMNB.png',
  KCAL: 'GYMTI-KCAL.png',
  HIDE: 'GYMTI-HIDE.png',
  LIFE: 'GYMTI-LIFE.png',
  CURV: 'GYMTI-CURV.png',
  BOOM: 'GYMTI-BOOM.png',
  WINN: 'GYMTI-WINNer.png'
};

const sessions = new Map();
let questions = [];

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const index = trimmed.indexOf('=');
    if (index === -1) continue;
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = value;
  }
}

function resolveQuestionnairePath(configuredPath, fallbackPath) {
  if (configuredPath && fs.existsSync(configuredPath)) return configuredPath;
  return fallbackPath;
}

async function loadQuestions() {
  const markdown = await fsp.readFile(QUESTIONNAIRE_PATH, 'utf8');
  questions = parseMarkdown(markdown);
  if (questions.length < 4) {
    throw new Error(`Parsed only ${questions.length} questions from ${QUESTIONNAIRE_PATH}`);
  }
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload, null, 2);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store'
  });
  res.end(body);
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw) return {};
  return JSON.parse(raw);
}

function notFound(res) {
  res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
  res.end('Not found');
}

function badRequest(res, message) {
  sendJson(res, 400, { error: message });
}

async function deepSeekChat(messages, { json = false, temperature = 0.2 } = {}) {
  const apiKey = process.env.DEEPSEEK_API_KEY;
  if (!apiKey) return null;

  const body = {
    model: process.env.DEEPSEEK_MODEL || 'deepseek-chat',
    messages,
    temperature
  };

  if (json) body.response_format = { type: 'json_object' };

  const response = await fetch(process.env.DEEPSEEK_API_URL || 'https://api.deepseek.com/chat/completions', {
    method: 'POST',
    headers: {
      authorization: `Bearer ${apiKey}`,
      'content-type': 'application/json'
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`DeepSeek API ${response.status}: ${text.slice(0, 300)}`);
  }

  const data = await response.json();
  return data.choices?.[0]?.message?.content || null;
}

function parseJsonObject(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) return null;
    try {
      return JSON.parse(match[0]);
    } catch {
      return null;
    }
  }
}

function sanitizeProfile(input = {}) {
  const heightCm = Number(input.heightCm);
  const weightKg = Number(input.weightKg);
  const allowedSex = new Set(['female', 'male', 'other', 'prefer_not_to_say']);
  const sex = allowedSex.has(input.sex) ? input.sex : '';
  const profile = {
    skipped: Boolean(input.skipped),
    heightCm: Number.isFinite(heightCm) && heightCm >= 80 && heightCm <= 230 ? Math.round(heightCm) : null,
    weightKg: Number.isFinite(weightKg) && weightKg >= 20 && weightKg <= 250 ? Math.round(weightKg * 10) / 10 : null,
    sex
  };
  profile.hasAny = Boolean(profile.heightCm || profile.weightKg || profile.sex);
  if (profile.heightCm && profile.weightKg) {
    const meters = profile.heightCm / 100;
    profile.bmi = Math.round((profile.weightKg / (meters * meters)) * 10) / 10;
  } else {
    profile.bmi = null;
  }
  return profile;
}

function sexLabel(sex) {
  return {
    female: '女',
    male: '男',
    other: '其他',
    prefer_not_to_say: '暂不说明'
  }[sex] || '未填写';
}

function buildProfileContext(profile = {}) {
  if (profile.skipped || !profile.hasAny) {
    return {
      usage: '这些信息已跳过。结果不会使用身体数据，只根据问卷回答匹配 GYMTI 和猫咪桌宠。',
      summary: '未填写个人信息',
      advice: '后续生成训练负荷时，建议先使用低风险默认负荷，并在用户完成 1-2 次训练反馈后再逐步校准。'
    };
  }

  const parts = [];
  if (profile.heightCm) parts.push(`身高 ${profile.heightCm} cm`);
  if (profile.weightKg) parts.push(`体重 ${profile.weightKg} kg`);
  if (profile.sex) parts.push(`性别 ${sexLabel(profile.sex)}`);
  if (profile.bmi) parts.push(`BMI ${profile.bmi}`);

  return {
    usage: '个人信息仅用于个性化健身负荷生成和训练建议，不会影响你的测评结果。',
    summary: parts.join(' · '),
    advice: profile.heightCm && profile.weightKg
      ? '后续可结合身高体重估算起始负荷、动作难度和恢复建议；首次训练仍建议以主观用力感和动作质量校准。'
      : '当前信息不完整，可用于轻度个性化建议；涉及负荷重量时仍建议从保守默认值开始。'
  };
}

async function chooseNextQuestion(state) {
  const unanswered = questions.filter((question) => !state.answeredIds.has(question.id));
  if (!unanswered.length) return { question: null, source: 'none', reason: '没有未答题目' };

  if (state.answerDetails.length === 0) {
    const opening = unanswered.find((question) => question.id === OPENING_QUESTION_ID)
      || unanswered.find((question) => OPENING_QUESTION_PAT.test(question.text));
    if (opening) return { question: opening, source: 'fixed-opening', reason: '固定开场题' };
  }

  try {
    const candidates = unanswered.map(publicCandidate);
    const content = await deepSeekChat([
      {
        role: 'system',
        content: [
          '你是一个自适应问卷出题器。',
          '目标：用最少问题同时判断用户的 GYMTI 健身人格和最适合的猫咪桌宠风格。',
          '只能从候选题中选择一道下一题。',
          '优先选择能区分当前分数接近类型的问题。',
          '返回严格 JSON：{"questionId":"q5","reason":"一句中文理由"}。'
        ].join('\n')
      },
      {
        role: 'user',
        content: JSON.stringify({
          answeredCount: state.answerDetails.length,
          currentGymtiRank: state.gymtiRank.slice(0, 4),
          currentPetRank: state.petRank.slice(0, 4),
          answered: state.answerDetails,
          candidates
        })
      }
    ], { json: true, temperature: 0.1 });

    const parsed = parseJsonObject(content);
    const selected = unanswered.find((question) => question.id === parsed?.questionId);
    if (selected) {
      return { question: selected, source: 'llm', reason: parsed.reason || 'LLM 选择了下一道最有信息量的问题' };
    }
  } catch (error) {
    console.warn('[llm-next-question-fallback]', error.message);
  }

  const fallback = chooseNextHeuristic(questions, state);
  return { question: fallback, source: 'heuristic', reason: 'LLM 不可用，使用本地信息增益规则选题' };
}

async function generateNarrative(result, state, profileContext) {
  const fallback = {
    headline: `${result.gymtiDisplay} ${result.gymtiName} × ${result.pet}`,
    summary: `${result.gymtiOneLiner} 你的桌宠适合走“${result.pet}”路线：${result.petOneLiner}${result.confidence === 'low' ? ' 这次“都不像”较多，结果建议当作弱匹配参考。' : ''}`,
    gymtiRead: `你目前最像 ${result.gymtiName}。系统会优先推荐：${result.gymtiAdvice.join('、')}。`,
    petRead: `这只猫应该${result.petVoice}`,
    nextActions: profileContext?.summary && profileContext.summary !== '未填写个人信息'
      ? result.gymtiAdvice.slice(0, 2).concat('个性化负荷校准')
      : result.gymtiAdvice.slice(0, 3),
    petLines: [
      result.pet === '专业数据型' ? '本组完成度不错，但下一组请把动作质量也算进胜利。' : '今天先开始，不需要把人生一次性练明白。',
      result.pet === '毒舌督促型' ? '借口生成器请暂停运行，本喵要检查训练记录。' : '回来就算赢，剩下的交给下一组。'
    ]
  };

  if (result.isTentative) {
    return {
      ...fallback,
      headline: '暂时还没测准，再给猫咪一点线索',
      summary: '这次有效偏好信号不足，系统不会强行把你归到 HIDE 或温柔陪伴型。建议再补答几题，或者选择更接近真实训练习惯的选项。',
      gymtiRead: result.gymtiOneLiner,
      petRead: result.petVoice,
      petLines: ['本次先不乱贴标签。', '多给我两三个真实反应，我再认真给你配猫。'],
      source: 'template'
    };
  }

  try {
    const content = await deepSeekChat([
      {
        role: 'system',
        content: [
          '你是 GYMTI 健身人格测试的结果解释器。',
          '语气：中文互联网人格锐评，像 SBTI 一样滑稽、具体，但不要羞辱身体、体重或运动能力。',
          '请解释为什么这些回答导向该健身人格和该猫咪桌宠。',
          '返回严格 JSON：headline, summary, gymtiRead, petRead, nextActions, petLines。',
          'nextActions 是 3 个短建议，petLines 是 2 句桌宠提示台词。'
        ].join('\n')
      },
      {
        role: 'user',
        content: JSON.stringify({
          result,
          confidence: result.confidence,
          noMatchCount: result.noMatchCount,
          topGymti: state.gymtiRank.slice(0, 4),
          topPet: state.petRank.slice(0, 4),
          answers: state.answerDetails,
          profileContext
        })
      }
    ], { json: true, temperature: 0.65 });

    if (!content) return { ...fallback, source: 'template' };
    return { ...fallback, ...parseJsonObject(content), source: 'llm' };
  } catch (error) {
    console.warn('[llm-narrative-fallback]', error.message);
    return { ...fallback, source: 'template' };
  }
}

function buildPetChoices(state, result) {
  const choices = state.petRank
    .filter((item) => PET_TYPES[item.key] && PET_AVATARS[item.key])
    .map((item) => ({
      type: item.key,
      oneLiner: PET_TYPES[item.key].oneLiner || '',
      voice: PET_TYPES[item.key].voice || '',
      initial: item.key === result.pet
    }));

  if (!choices.length && PET_TYPES[result.pet] && PET_AVATARS[result.pet]) {
    choices.push({
      type: result.pet,
      oneLiner: PET_TYPES[result.pet].oneLiner || '',
      voice: PET_TYPES[result.pet].voice || '',
      initial: true
    });
  }

  return choices;
}

async function buildTurnResponse(session, forceFinish = false) {
  const state = summarizeState(questions, session.answers);
  const done = forceFinish || shouldFinish(state, MIN_QUESTIONS, MAX_QUESTIONS);
  const profileContext = buildProfileContext(session.profile);

  if (done) {
    const result = buildFinalResult(state);
    const narrative = await generateNarrative(result, state, profileContext);
    return {
      done: true,
      answeredCount: state.answerDetails.length,
      minQuestions: MIN_QUESTIONS,
      maxQuestions: MAX_QUESTIONS,
      profile: session.profile,
      profileContext,
      result,
      narrative,
      petChoices: buildPetChoices(state, result),
      noMatchCount: state.noMatchCount,
      rankings: {
        gymti: state.gymtiRank.slice(0, 5),
        pet: state.petRank.slice(0, 5)
      }
    };
  }

  const next = await chooseNextQuestion(state);
  return {
    done: false,
    answeredCount: state.answerDetails.length,
    minQuestions: MIN_QUESTIONS,
    maxQuestions: MAX_QUESTIONS,
    profile: session.profile,
    profileContext,
    question: questionToPublic(next.question),
    chooser: {
      source: next.source,
      reason: next.reason
    },
    canFinish: state.answerDetails.length >= MIN_QUESTIONS
  };
}

async function handleApi(req, res, pathname) {
  if (req.method === 'GET' && pathname === '/api/health') {
    sendJson(res, 200, {
      ok: true,
      questionCount: questions.length,
      questionnairePath: QUESTIONNAIRE_PATH,
      llmEnabled: Boolean(process.env.DEEPSEEK_API_KEY),
      minQuestions: MIN_QUESTIONS,
      maxQuestions: MAX_QUESTIONS
    });
    return;
  }

  if (req.method === 'POST' && pathname === '/api/start') {
    const sessionId = crypto.randomUUID();
    const session = { id: sessionId, answers: [], profile: sanitizeProfile({ skipped: true }) };
    sessions.set(sessionId, session);
    const response = await buildTurnResponse(session);
    sendJson(res, 200, { sessionId, ...response });
    return;
  }

  if (req.method === 'POST' && pathname === '/api/profile') {
    const body = await readJson(req);
    const session = sessions.get(body.sessionId);
    if (!session) return badRequest(res, 'Invalid sessionId');
    session.profile = sanitizeProfile(body.profile || {});
    const response = await buildTurnResponse(session, true);
    sendJson(res, 200, response);
    return;
  }

  if (req.method === 'POST' && pathname === '/api/answer') {
    const body = await readJson(req);
    const session = sessions.get(body.sessionId);
    if (!session) return badRequest(res, 'Invalid sessionId');

    const question = questions.find((item) => item.id === body.questionId);
    const option = body.optionId === NONE_OPTION_ID
      ? { id: NONE_OPTION_ID }
      : question?.options.find((item) => item.id === body.optionId);
    if (!question || !option) return badRequest(res, 'Invalid questionId or optionId');
    const existingAnswer = session.answers.find((answer) => answer.questionId === body.questionId);
    if (existingAnswer) {
      if (existingAnswer.optionId !== body.optionId) {
        return badRequest(res, 'Question already answered');
      }
      const response = await buildTurnResponse(session);
      sendJson(res, 200, response);
      return;
    }

    session.answers.push({ questionId: body.questionId, optionId: body.optionId });
    const response = await buildTurnResponse(session);
    sendJson(res, 200, response);
    return;
  }

  if (req.method === 'POST' && pathname === '/api/finish') {
    const body = await readJson(req);
    const session = sessions.get(body.sessionId);
    if (!session) return badRequest(res, 'Invalid sessionId');
    const response = await buildTurnResponse(session, true);
    sendJson(res, 200, response);
    return;
  }

  notFound(res);
}

function contentTypeFor(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml'
  }[ext] || 'application/octet-stream';
}

async function serveStatic(res, pathname) {
  const safePath = pathname === '/' ? '/index.html' : pathname;
  const resolved = path.resolve(PUBLIC_DIR, `.${safePath}`);
  if (!resolved.startsWith(PUBLIC_DIR)) return notFound(res);

  try {
    const data = await fsp.readFile(resolved);
    res.writeHead(200, { 'content-type': contentTypeFor(resolved) });
    res.end(data);
  } catch {
    notFound(res);
  }
}

async function serveAvatar(req, res, url) {
  const type = url.searchParams.get('type');
  const file = PET_AVATARS[type];
  if (!file) return notFound(res);
  const resolved = path.resolve(CAT_AVATAR_DIR, file);
  if (!resolved.startsWith(path.resolve(CAT_AVATAR_DIR))) return notFound(res);
  if (!fs.existsSync(resolved)) return notFound(res);
  res.writeHead(200, { 'content-type': contentTypeFor(resolved), 'cache-control': 'public, max-age=3600' });
  fs.createReadStream(resolved).pipe(res);
}

async function serveGymtiImage(req, res, url) {
  const type = url.searchParams.get('type');
  const file = GYMTI_IMAGES[type];
  if (!file) return notFound(res);
  const resolved = path.resolve(GYMTI_IMAGE_DIR, file);
  if (!resolved.startsWith(path.resolve(GYMTI_IMAGE_DIR))) return notFound(res);
  if (!fs.existsSync(resolved)) return notFound(res);
  res.writeHead(200, { 'content-type': contentTypeFor(resolved), 'cache-control': 'public, max-age=3600' });
  fs.createReadStream(resolved).pipe(res);
}

async function handleRequest(req, res) {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    if (url.pathname.startsWith('/api/')) {
      await handleApi(req, res, url.pathname);
      return;
    }
    if (url.pathname === '/avatar') {
      await serveAvatar(req, res, url);
      return;
    }
    if (url.pathname === '/gymti-image') {
      await serveGymtiImage(req, res, url);
      return;
    }
    await serveStatic(res, url.pathname);
  } catch (error) {
    console.error(error);
    sendJson(res, 500, { error: error.message });
  }
}

loadQuestions()
  .then(() => {
    http.createServer(handleRequest).listen(PORT, () => {
      console.log(`GYMTI pet quiz running at http://localhost:${PORT}`);
      console.log(`Parsed ${questions.length} questions from ${QUESTIONNAIRE_PATH}`);
      console.log(`LLM enabled: ${Boolean(process.env.DEEPSEEK_API_KEY)}`);
    });
  })
  .catch((error) => {
    console.error(error);
    process.exit(1);
  });
