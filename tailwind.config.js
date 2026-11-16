/** @type {import('tailwindcss').Config} */
module.exports = {
  // クラス名は templates 内のHTML/JS文字列のほか、app.py（称号の色クラス）にも書かれている
  content: ["./templates/**/*.html", "./app.py", "./data/questions.json"],
  theme: { extend: {} },
  plugins: [],
};
