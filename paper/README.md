# 中文建模论文初稿

- `paper.pdf`：已编译论文，第四问模型与解法实际留白，插图为方框文字描述。
- `main.tex`：主文件，`sections/` 分章，便于继续修改。
- `code/`：附录摘录、有限兜底网格原型与已有核心代码快照。
- `verify_formulas.py`：离线数学复核，不连接任何模拟器。
- `build/formula-checks.json`：关键公式复核数值。
- `MODEL_NOTES.md`：论文与现有实现的差异、后续需要补齐的工作。

使用本机已有 TeX Live 2026/Debian 的 LuaLaTeX 编译。未安装 ctex/xeCJK，因此使用 `article + babel(chinese) + fontspec`，字体为本机 Noto Serif CJK SC / Noto Sans CJK SC。无需新安装 TeX 包。

在仓库根目录运行：

```bash
bash paper/build.sh
python3 paper/verify_formulas.py
```

编译脚本自动切换至论文目录、编译三次并生成 `paper/paper.pdf`。TeX 字体缓存默认写到 `/tmp/mathmodel-tex-cache`，可以用环境变量 `TEXMFVAR` 指定其他可写目录。编译日志和中间文件在 `build/`。

内容以知识和推导为主，采用常见中文建模论文的摘要、问题分析、假设、符号、分问建模、结果、评价、参考文献、推导及代码附录结构。没有替代你们做最终格式审定；封面/承诺页、作者信息与比赛最终格式由你们按要求处理。

已引用资料均为仓库中给出的题面及附件，数学结论在附录自证。未把仓库其他说明中的未经核实理论陈述和概率数值当作定论。没有登录、调用演练接口或占用正式测试机会，没有修改现有算法源码。

已有30例模拟数值取自 `_bench_v2_q3.txt`，明确标为历史本地记录，未冒充官方成绩。正式表格待填。代码快照保留原实现和注释，其中存在的数学问题请以 `MODEL_NOTES.md` 和论文推导为准。
