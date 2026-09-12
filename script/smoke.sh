#!/usr/bin/env bash
# 一键体检：把整条工作流该跑的都跑一遍，用来确认"当前环境还能用"。
#
#   bash script/smoke.sh            # 离线全流程（不需要 Windows 客户端）
#   bash script/smoke.sh --online   # 额外做线上连通性预检
#   bash script/smoke.sh --ui       # 额外跑题目2 界面的真浏览器端到端测试
#
# 退出码 0 表示全部通过。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRAMEWORK="${REPO_ROOT}/framework"

PYTHON="${JAMMERS_PYTHON:-}"
if [[ -z "${PYTHON}" ]]; then
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then PYTHON="${candidate}"; break; fi
  done
fi
if [[ -z "${PYTHON}" ]]; then
  echo "找不到 Python 解释器；可用 pyenv 安装 3.13，并 export JAMMERS_PYTHON=..." >&2
  exit 3
fi

ONLINE=0
UI=0
for arg in "$@"; do
  [[ "${arg}" == "--online" ]] && ONLINE=1
  [[ "${arg}" == "--ui" ]] && UI=1
done

fail=0
step() { printf '\n=== %s ===\n' "$1"; }
check() {
  if [[ "$1" -eq 0 ]]; then
    printf '  [OK]   %s\n' "$2"
  else
    printf '  [FAIL] %s\n' "$2"
    fail=1
  fi
}

export PYTHONPATH="${FRAMEWORK}/src:${SCRIPT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

step "0. 解释器"
echo "  python : $("${PYTHON}" -V 2>&1)  ->  $(command -v "${PYTHON}")"
echo "  repo   : ${REPO_ROOT}"

step "1. login-jammers 路径解析（不写死路径）"
"${PYTHON}" "${SCRIPT_DIR}/jammers_paths.py"
check $? "路径解析"

step "2. 框架单元/集成测试"
(cd "${FRAMEWORK}" && "${PYTHON}" -m pytest tests -q)
check $? "pytest"

step "3. 离线 mock 端到端（覆盖扫描 + 交会定位 + 清除）"
"${PYTHON}" "${SCRIPT_DIR}/run.py" --seed 3 --tag smoke --quiet
check $? "run.py --mode mock"

step "4. 小批量调参（3 半径 x 3 种子）"
"${PYTHON}" "${SCRIPT_DIR}/run_batch.py" --tag smoke --seeds 1-3 \
  --grid scan_radius=1150,1200,1400 --jobs 3 --require-full-clear --quiet
check $? "run_batch.py"

step "5. 结果汇总"
"${PYTHON}" "${SCRIPT_DIR}/report.py" --tag smoke --last 4
check $? "report.py"

step "5.1 题目2 知识矩阵几何与 HTTP API 单测"
(cd "${REPO_ROOT}" && PYTHONPATH="${REPO_ROOT}/cpp" "${PYTHON}" -m unittest discover \
  -s "${SCRIPT_DIR}" -p 'test_q2_matrix*.py')
check $? "test_q2_matrix"

if [[ "${UI}" -eq 1 ]]; then
  step "5.2 题目2 界面真浏览器端到端测试"
  if command -v node >/dev/null 2>&1; then
    node "${SCRIPT_DIR}/q2_ui_e2e_test.js" --port 8071
    check $? "q2_ui_e2e_test.js"
  else
    printf '  [SKIP] 没有 node，跳过界面端到端测试\n'
  fi
fi

if [[ "${ONLINE}" -eq 1 ]]; then
  step "6. 线上连通性预检（login-jammers CLI -> 平台）"
  "${PYTHON}" "${SCRIPT_DIR}/preflight.py"
  rc=$?
  # 1 = robot 端口未开（正常，没有 Windows 客户端），2 = 业务码（链路通），都不算失败
  if [[ "${rc}" -le 2 ]]; then
    printf '  [OK]   preflight（退出码 %d；1=端口未开, 2=业务码，均属链路可达）\n' "${rc}"
  else
    printf '  [FAIL] preflight（退出码 %d）\n' "${rc}"
    fail=1
  fi
fi

printf '\n=== 结论 ===\n'
if [[ "${fail}" -eq 0 ]]; then
  echo "  全部通过。"
else
  echo "  有步骤失败，见上面 [FAIL]。"
fi
exit "${fail}"
