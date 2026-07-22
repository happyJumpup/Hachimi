const GYMTI_TYPES = {
  IMNB: {
    name: '肌！肉！人！',
    oneLiner: '你不是在训练，你是在给肌肉部门开季度增长会。',
    advice: ['渐进超负荷计划', 'PR 记录', '训练容量统计', '恢复日提醒']
  },
  KCAL: {
    name: '卡路里收纳师',
    oneLiner: '你不是在减脂，你是在给卡路里办理集体销户。',
    advice: ['体重趋势线', '饮食记录', '照片对比', '燃脂课程']
  },
  HIDE: {
    name: '器械区躲猫猫小不点',
    oneLiner: '你不是不想练，你是在等全世界先把目光移开。',
    advice: ['新手动作拆解', '器械说明', '私密模式', '5 分钟入门任务']
  },
  LIFE: {
    name: '续命打工人',
    oneLiner: '你不是佛系，你是在进行现代人体维修。',
    advice: ['肩颈腰背课程', '久坐提醒', '低强度力量', '睡眠与疲劳记录']
  },
  CURV: {
    name: '体态氛围组 / 线条精修师',
    oneLiner: '你不是怕变强，你是要把力量调成好看的参数。',
    advice: ['体态改善', '臀腿核心', '普拉提轻力量', '拍照对比']
  },
  BOOM: {
    name: '老暴汗家',
    oneLiner: '你不是在暴汗，你是在给情绪开排气阀。',
    advice: ['高燃团课', 'HIIT', '音乐节奏课程', '课后情绪记录']
  },
  WINN: {
    name: '赢麻者',
    display: 'WINN-er',
    oneLiner: '你不是随便运动，你是在用数据偷偷炼丹。',
    advice: ['赛事周期', '训练负荷', '恢复分析', 'PB 预测']
  }
};

const PET_TYPES = {
  热血鼓励型: {
    oneLiner: '适合需要氛围点火、被积极带动的人。',
    voice: '热烈、积极、像拿着小旗子冲进训练现场。'
  },
  温柔陪伴型: {
    oneLiner: '适合容易自责、怕尴尬、需要安全感的人。',
    voice: '轻声提醒，先接住你，再带你往前走。'
  },
  毒舌督促型: {
    oneLiner: '适合能接受轻微吐槽，越被损越想证明自己的人。',
    voice: '嘴上不饶人，但不攻击身体和羞耻点。'
  },
  专业数据型: {
    oneLiner: '适合相信数据、计划、动作反馈和长期趋势的人。',
    voice: '报表清晰，建议具体，每句话都像有依据。'
  },
  幽默话痨型: {
    oneLiner: '适合怕无聊、需要陪伴感和情绪缓冲的人。',
    voice: '用离谱但有用的吐槽，把训练变得没那么难熬。'
  },
  挑战突破型: {
    oneLiner: '适合临门一脚需要被推一把的人。',
    voice: '短句、压迫感、关键时刻把你从放弃边缘拉回来。'
  },
  佛系陪练型: {
    oneLiner: '适合低压力启动、恢复训练、健康续命型用户。',
    voice: '不审判、不催命，主打今天先开机。'
  }
};

const PET_AVATARS = {
  热血鼓励型: '1b764e5dc9d779a234e65f48b2f1900d.png',
  温柔陪伴型: 'aad9936d596852f4e784dd17f979a9a6.png',
  毒舌督促型: 'Pasted image 20260722174114.png',
  专业数据型: 'Pasted image 20260722174146.png',
  幽默话痨型: 'Pasted image 20260722220319.png',
  挑战突破型: 'Pasted image 20260722174249.png',
  佛系陪练型: 'Pasted image 20260722174314.png'
};

const GYMTI_CODES = Object.keys(GYMTI_TYPES);
const PET_NAMES = Object.keys(PET_TYPES);
const NONE_OPTION_ID = '__none__';
const NONE_OPTION_LABEL = '没有符合我的描述';

function emptyScores() {
  return {
    gymti: Object.fromEntries(GYMTI_CODES.map((code) => [code, 0])),
    pet: Object.fromEntries(PET_NAMES.map((name) => [name, 0])),
    petBans: new Set()
  };
}

function addScore(target, key, value) {
  if (!Object.prototype.hasOwnProperty.call(target, key)) return;
  target[key] += Number(value || 0);
}

function parseScoreText(scoreText) {
  const scores = { gymti: {}, pet: {}, petBans: [] };
  const gymtiRegex = /\b(IMNB|KCAL|HIDE|LIFE|CURV|BOOM|WINN)\b\s*([+-]\d+)/g;
  const petRegex = /(热血鼓励型|温柔陪伴型|毒舌督促型|专业数据型|幽默话痨型|挑战突破型|佛系陪练型)\s*([+-]\d+)/g;
  const banRegex = /(热血鼓励型|温柔陪伴型|毒舌督促型|专业数据型|幽默话痨型|挑战突破型|佛系陪练型)\s*定为不可能/g;

  for (const match of scoreText.matchAll(gymtiRegex)) {
    scores.gymti[match[1]] = (scores.gymti[match[1]] || 0) + Number(match[2]);
  }

  for (const match of scoreText.matchAll(petRegex)) {
    scores.pet[match[1]] = (scores.pet[match[1]] || 0) + Number(match[2]);
  }

  for (const match of scoreText.matchAll(banRegex)) {
    scores.petBans.push(match[1]);
  }

  return scores;
}

function parseMarkdown(markdown) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const questions = [];
  let current = null;
  let currentOption = null;

  function flushOption() {
    if (!current || !currentOption) return;
    const parsed = parseScoreText(currentOption.scoreText.join(' '));
    current.options.push({
      id: currentOption.id,
      label: currentOption.label.trim(),
      scores: parsed
    });
    currentOption = null;
  }

  function flushQuestion() {
    if (!current) return;
    flushOption();
    if (current.text && current.options.length >= 2) {
      current.gymtiTargets = distinctTargets(current.options, 'gymti');
      current.petTargets = distinctTargets(current.options, 'pet');
      questions.push(current);
    }
    current = null;
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const qMatch = line.match(/^###\s+(\d+)\.\s+(.+)$/);
    if (qMatch) {
      flushQuestion();
      current = {
        id: `q${qMatch[1]}`,
        number: Number(qMatch[1]),
        text: qMatch[2].trim(),
        options: []
      };
      continue;
    }

    if (!current) continue;

    const optionMatch = line.match(/^([A-Z])\.\s+(.+)$/);
    if (optionMatch) {
      flushOption();
      currentOption = {
        id: optionMatch[1],
        label: optionMatch[2],
        scoreText: []
      };
      continue;
    }

    if (currentOption && line) {
      currentOption.scoreText.push(line);
    }
  }

  flushQuestion();
  return questions.sort((a, b) => a.number - b.number);
}

function distinctTargets(options, bucket) {
  const keys = new Set();
  for (const option of options) {
    for (const key of Object.keys(option.scores[bucket] || {})) keys.add(key);
  }
  return [...keys];
}

function applyAnswers(questions, answers) {
  const score = emptyScores();
  const answerDetails = [];
  let noMatchCount = 0;

  for (const answer of answers) {
    const question = questions.find((item) => item.id === answer.questionId);
    if (!question) continue;

    if (answer.optionId === NONE_OPTION_ID) {
      noMatchCount += 1;
      answerDetails.push({
        questionId: question.id,
        question: question.text,
        optionId: NONE_OPTION_ID,
        option: NONE_OPTION_LABEL,
        noMatch: true
      });
      continue;
    }

    const option = question.options.find((item) => item.id === answer.optionId);
    if (!option) continue;

    for (const [key, value] of Object.entries(option.scores.gymti || {})) {
      addScore(score.gymti, key, value);
    }
    for (const [key, value] of Object.entries(option.scores.pet || {})) {
      addScore(score.pet, key, value);
    }
    for (const petName of option.scores.petBans || []) {
      score.petBans.add(petName);
    }

    answerDetails.push({
      questionId: question.id,
      question: question.text,
      optionId: option.id,
      option: option.label
    });
  }

  for (const petName of score.petBans) {
    score.pet[petName] = Number.NEGATIVE_INFINITY;
  }

  return { score, answerDetails, noMatchCount };
}

function rankScores(scoreObject) {
  return Object.entries(scoreObject)
    .filter(([, value]) => Number.isFinite(value))
    .sort((a, b) => b[1] - a[1])
    .map(([key, value], index, list) => ({
      key,
      value,
      gapToNext: index < list.length - 1 ? value - list[index + 1][1] : value
    }));
}

function summarizeState(questions, answers) {
  const { score, answerDetails, noMatchCount } = applyAnswers(questions, answers);
  const gymtiRank = rankScores(score.gymti);
  const petRank = rankScores(score.pet);
  return {
    score,
    answerDetails,
    noMatchCount,
    gymtiRank,
    petRank,
    answeredIds: new Set(answers.map((answer) => answer.questionId))
  };
}

function shouldFinish(state, minQuestions, maxQuestions) {
  const answered = state.answerDetails.length;
  if (answered >= maxQuestions) return true;
  if (answered < minQuestions) return false;
  if (state.noMatchCount >= Math.ceil(answered / 2)) return false;

  const gymtiGap = (state.gymtiRank[0]?.value || 0) - (state.gymtiRank[1]?.value || 0);
  const petGap = (state.petRank[0]?.value || 0) - (state.petRank[1]?.value || 0);
  const gymtiEnough = (state.gymtiRank[0]?.value || 0) >= 4 && gymtiGap >= 3;
  const petEnough = (state.petRank[0]?.value || 0) >= 4 && petGap >= 3;
  return gymtiEnough && petEnough;
}

function questionToPublic(question) {
  const noneOption = noneOptionForQuestion(question);
  return {
    id: question.id,
    number: question.number,
    text: question.text,
    options: question.options.map((option) => ({
      id: option.id,
      displayId: option.id,
      label: option.label
    })).concat(noneOption)
  };
}

function noneOptionForQuestion(question) {
  return {
    id: NONE_OPTION_ID,
    displayId: '其他',
    label: NONE_OPTION_LABEL,
    noMatch: true
  };
}

function chooseNextHeuristic(questions, state) {
  const unanswered = questions.filter((question) => !state.answeredIds.has(question.id));
  if (unanswered.length === 0) return null;

  const gymtiGap = (state.gymtiRank[0]?.value || 0) - (state.gymtiRank[1]?.value || 0);
  const petGap = (state.petRank[0]?.value || 0) - (state.petRank[1]?.value || 0);
  const needPet = petGap < gymtiGap || state.answerDetails.length >= 3;

  const scored = unanswered.map((question) => {
    const gymtiSpread = spreadForQuestion(question, 'gymti');
    const petSpread = spreadForQuestion(question, 'pet');
    const targetCount = question.gymtiTargets.length + question.petTargets.length;
    const latePetBonus = needPet && question.number >= 15 ? 4 : 0;
    const earlyBroadBonus = state.answerDetails.length === 0 && question.gymtiTargets.length >= 3 ? 4 : 0;
    return {
      question,
      score: gymtiSpread * 1.5 + petSpread * 1.2 + targetCount + latePetBonus + earlyBroadBonus
    };
  });

  scored.sort((a, b) => b.score - a.score || a.question.number - b.question.number);
  return scored[0].question;
}

function spreadForQuestion(question, bucket) {
  const totals = question.options.map((option) => {
    return Object.values(option.scores[bucket] || {}).reduce((sum, value) => sum + value, 0);
  });
  if (!totals.length) return 0;
  return Math.max(...totals) - Math.min(...totals);
}

function buildFinalResult(state) {
  const topGymtiScore = state.gymtiRank[0]?.value || 0;
  const topPetScore = state.petRank[0]?.value || 0;
  const hasGymtiSignal = topGymtiScore > 0;
  const hasPetSignal = topPetScore > 0;
  const gymti = hasGymtiSignal ? state.gymtiRank[0]?.key : 'UNSET';
  const gymtiSecondary = hasGymtiSignal && state.gymtiRank[1] && state.gymtiRank[0].value - state.gymtiRank[1].value <= 1
    ? state.gymtiRank[1].key
    : null;
  const pet = hasPetSignal ? state.petRank[0]?.key : '待确认猫咪';
  const petSecondary = hasPetSignal && state.petRank[1] && state.petRank[0].value - state.petRank[1].value <= 1
    ? state.petRank[1].key
    : null;
  const confidence = !hasGymtiSignal || !hasPetSignal || state.noMatchCount >= 3 || topGymtiScore < 3 || topPetScore < 3
    ? 'low'
    : state.noMatchCount >= 1
      ? 'medium'
      : 'high';
  const isTentative = !hasGymtiSignal || !hasPetSignal;

  return {
    gymti,
    gymtiName: hasGymtiSignal ? GYMTI_TYPES[gymti]?.name || gymti : '暂不定型',
    gymtiDisplay: hasGymtiSignal ? GYMTI_TYPES[gymti]?.display || gymti : '待确认',
    gymtiSecondary,
    gymtiSecondaryName: gymtiSecondary ? GYMTI_TYPES[gymtiSecondary]?.name : null,
    gymtiOneLiner: hasGymtiSignal ? GYMTI_TYPES[gymti]?.oneLiner || '' : '这次有效倾向信号不足，暂时不把你硬塞进某一种 GYMTI。',
    gymtiAdvice: hasGymtiSignal ? GYMTI_TYPES[gymti]?.advice || [] : ['再补答几题', '选择最接近真实训练习惯的选项', '先用轻量通用计划'],
    pet,
    petSecondary,
    petOneLiner: hasPetSignal ? PET_TYPES[pet]?.oneLiner || '' : '桌宠语气还需要更多偏好信号，先不给你乱配猫。',
    petVoice: hasPetSignal ? PET_TYPES[pet]?.voice || '' : '先保持中性、低压力、少打扰，等用户给出更明确偏好后再切换语气。',
    petAvatarFile: PET_AVATARS[pet] || null,
    confidence,
    isTentative,
    noMatchCount: state.noMatchCount,
    rawScores: {
      gymti: Object.fromEntries(state.gymtiRank.map((item) => [item.key, item.value])),
      pet: Object.fromEntries(state.petRank.map((item) => [item.key, item.value]))
    }
  };
}

function publicCandidate(question) {
  const noneOption = noneOptionForQuestion(question);
  return {
    id: question.id,
    number: question.number,
    text: question.text,
    options: question.options.map((option) => ({
      id: option.id,
      displayId: option.id,
      label: option.label,
      gymtiTargets: Object.keys(option.scores.gymti || {}),
      petTargets: Object.keys(option.scores.pet || {})
    })).concat({
      id: noneOption.id,
      displayId: noneOption.displayId,
      label: noneOption.label,
      gymtiTargets: [],
      petTargets: [],
      noMatch: true
    })
  };
}

module.exports = {
  GYMTI_TYPES,
  PET_TYPES,
  PET_AVATARS,
  NONE_OPTION_ID,
  NONE_OPTION_LABEL,
  parseMarkdown,
  summarizeState,
  shouldFinish,
  questionToPublic,
  chooseNextHeuristic,
  buildFinalResult,
  publicCandidate
};
