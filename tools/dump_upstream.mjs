// 从上游 pcl-homepage 仓库导出纯数据为 JSON（一次性移植工具）。
//
// 用法：
//   node tools/dump_upstream.mjs <上游仓库路径> > build/upstream.json
//
// 之所以借用 node 而不是在 Python 里解析 JS 字面量，是为了保证移植零误差：
// 数据在 JS 运行时里真实求值一遍再序列化，转义、引号、对象简写都不会出错。
// 导出的 JSON 由 tools/port_data.py 转写成 pclhome/data/*.py。

const repo = process.argv[2];
if (!repo) {
  console.error("用法：node tools/dump_upstream.mjs <上游仓库路径>");
  process.exit(2);
}

const url = (p) => new URL(p, "file:///" + repo.replace(/\\/g, "/").replace(/\/?$/, "/")).href;

const content = await import(url("functions/_lib/content.js"));
const quiz = await import(url("functions/_lib/quiz.js"));
const lunar = await import(url("functions/_lib/lunar.js"));

const out = {
  QUOTES: content.QUOTES,
  EGGS: content.EGGS,
  GREETING_SUBS: content.GREETING_SUBS,
  COLORS: content.COLORS,
  FORTUNE_GOOD: content.FORTUNE_GOOD,
  FORTUNE_BAD: content.FORTUNE_BAD,
  FORTUNE_TIPS: content.FORTUNE_TIPS,
  CHALLENGES: content.CHALLENGES,
  SEEDS: content.SEEDS,
  SCORE_COMMENTS: content.SCORE_COMMENTS,
  QUIZ: quiz.QUIZ,
  FESTIVALS: lunar.FESTIVALS,
  LUNAR_FESTIVALS: lunar.LUNAR_FESTIVALS,
  LUNAR_INFO: lunar.LUNAR_INFO,
};

process.stdout.write(JSON.stringify(out));
